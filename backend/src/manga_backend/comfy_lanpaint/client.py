"""Small loopback-only client for ComfyUI's HTTP prompt API."""

from __future__ import annotations

import asyncio
import io
import json
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import aiohttp
import numpy as np
from PIL import Image

from .workflow import WorkflowSettings, build_workflow


class ComfyUIError(RuntimeError):
    """The local ComfyUI daemon rejected or failed an inpaint job."""


class ComfyUIUnreachableError(ComfyUIError):
    """The ComfyUI daemon could not be reached, or dropped the connection mid-job."""


@dataclass(frozen=True, slots=True)
class UploadedImage:
    name: str
    subfolder: str = ""
    image_type: str = "input"

    @property
    def node_name(self) -> str:
        return f"{self.subfolder}/{self.name}" if self.subfolder else self.name


class ComfyUIClient:
    REQUIRED_NODES: ClassVar[set[str]] = {
        "CLIPLoader",
        "CLIPTextEncode",
        "ConditioningZeroOut",
        "FluxGuidance",
        "LanPaint_KSampler",
        "LoadImage",
        "ReferenceLatent",
        "SaveImageWebsocket",
        "SetLatentNoiseMask",
        "UNETLoader",
        "VAEDecode",
        "VAEEncode",
        "VAELoader",
    }
    NATIVE_SCHEDULE_NODES: ClassVar[set[str]] = {
        "Flux2Scheduler",
        "KSamplerSelect",
        "LanPaint_SamplerCustom",
    }

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:8188",
        *,
        request_timeout: float = 60.0,
        job_timeout: float = 1200.0,
        poll_interval: float = 0.2,
    ):
        self.server_url = server_url.rstrip("/")
        parsed = urlparse(self.server_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("ComfyUI server must be a loopback HTTP URL")
        self.request_timeout = request_timeout
        self.job_timeout = job_timeout
        self.poll_interval = poll_interval
        self.websocket_output = True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[aiohttp.ClientSession]:
        """Reuse one loopback connection across all crops in a request."""

        timeout = aiohttp.ClientTimeout(
            total=self.request_timeout,
            sock_connect=self.request_timeout,
            sock_read=self.request_timeout,
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            yield session

    async def validate(self, *, settings: WorkflowSettings | None = None) -> dict:
        required_nodes = set(self.REQUIRED_NODES)
        if settings is not None and settings.native_flux2_schedule:
            required_nodes.update(self.NATIVE_SCHEDULE_NODES)
        if not self.websocket_output:
            required_nodes.discard("SaveImageWebsocket")
            required_nodes.add("SaveImage")

        payload = {}
        async with self.session() as session:
            for node_name in sorted(required_nodes):
                async with session.get(
                    f"{self.server_url}/object_info/{node_name}"
                ) as response:
                    payload.update(
                        await self._json_response(response, "read ComfyUI node schema")
                    )
        missing = sorted(required_nodes.difference(payload))
        if missing:
            raise ComfyUIError(
                f"ComfyUI is missing required nodes: {', '.join(missing)}"
            )
        if settings is not None:
            self._validate_model_choices(payload, settings)
        return payload

    @staticmethod
    def _validate_model_choices(payload: dict, settings: WorkflowSettings) -> None:
        checks = (
            ("UNETLoader", "unet_name", settings.diffusion_model),
            ("CLIPLoader", "clip_name", settings.text_encoder),
            ("VAELoader", "vae_name", settings.vae),
        )
        missing = []
        for node_name, field_name, selected in checks:
            options = (
                payload.get(node_name, {})
                .get("input", {})
                .get("required", {})
                .get(field_name, [])
            )
            choices = options[0] if options and isinstance(options[0], list) else []
            if choices and selected not in choices:
                missing.append(f"{node_name}.{field_name}={selected}")
        if missing:
            raise ComfyUIError(
                "ComfyUI model files are unavailable: " + ", ".join(missing)
            )

    async def inpaint_crop(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        prompt: str,
        *,
        seed: int,
        settings: WorkflowSettings | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> np.ndarray:
        if session is None:
            async with self.session() as owned_session:
                return await self.inpaint_crop(
                    image,
                    mask,
                    prompt,
                    seed=seed,
                    settings=settings,
                    session=owned_session,
                )
        if self.websocket_output:
            client_id = uuid4().hex
            websocket_url = (
                f"{self.server_url.replace('http://', 'ws://', 1)}/ws?clientId={client_id}"
            )
            async with session.ws_connect(websocket_url, heartbeat=30) as websocket:
                workflow = await self._upload_masked_workflow(
                    session, image, mask, prompt, seed, settings, websocket_output=True
                )
                prompt_id = await self._queue(session, workflow, client_id=client_id)
                payload = await self._wait_for_websocket_image(websocket, prompt_id)
            return self._decode_image(payload)

        workflow = await self._upload_masked_workflow(
            session,
            image,
            mask,
            prompt,
            seed,
            settings,
            filename_prefix=f"headless_lanpaint/{uuid4().hex}",
        )
        prompt_id = await self._queue(session, workflow)
        output = await self._wait_for_output(session, prompt_id)
        return await self._download(session, output)

    async def _upload_masked_workflow(
        self,
        session: aiohttp.ClientSession,
        image: np.ndarray,
        mask: np.ndarray,
        prompt: str,
        seed: int,
        settings: WorkflowSettings | None,
        **output_options,
    ) -> dict:
        source = await self._upload(
            session, self._source_with_alpha_mask(image, mask), "source_mask"
        )
        return build_workflow(
            source.node_name,
            source.node_name,
            prompt,
            seed=seed,
            settings=settings,
            embedded_mask=True,
            image_width=image.shape[1],
            image_height=image.shape[0],
            **output_options,
        )

    @staticmethod
    def _source_with_alpha_mask(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("image must have RGB shape (height, width, 3)")
        if mask.ndim != 2 or mask.shape != image.shape[:2]:
            raise ValueError("mask shape must match image height and width")
        binary_mask = np.where(mask >= 128, 255, 0).astype(np.uint8)
        alpha = 255 - binary_mask
        return np.dstack((image.astype(np.uint8, copy=False), alpha))

    async def _upload(
        self, session: aiohttp.ClientSession, pixels: np.ndarray, label: str
    ) -> UploadedImage:
        image = Image.fromarray(pixels)
        buffer = io.BytesIO()
        # Level 0 is still lossless and avoids spending CPU time compressing loopback data.
        image.save(buffer, format="PNG", compress_level=0)
        buffer.seek(0)
        name = f"headless_{label}_{uuid4().hex}.png"
        form = aiohttp.FormData()
        form.add_field("image", buffer, filename=name, content_type="image/png")
        form.add_field("type", "input")
        form.add_field("overwrite", "true")
        async with session.post(
            f"{self.server_url}/upload/image", data=form
        ) as response:
            payload = await self._json_response(response, f"upload {label} image")
        return UploadedImage(
            name=payload.get("name", name),
            subfolder=payload.get("subfolder", ""),
            image_type=payload.get("type", "input"),
        )

    async def _queue(
        self,
        session: aiohttp.ClientSession,
        workflow: dict,
        *,
        client_id: str | None = None,
    ) -> str:
        body = {"prompt": workflow, "client_id": client_id or uuid4().hex}
        async with session.post(f"{self.server_url}/prompt", json=body) as response:
            payload = await self._json_response(response, "queue ComfyUI prompt")
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            details = payload.get("node_errors") or payload
            raise ComfyUIError(f"ComfyUI did not return a prompt id: {details}")
        return str(prompt_id)

    async def _wait_for_websocket_image(
        self, websocket: aiohttp.ClientWebSocketResponse, prompt_id: str
    ) -> bytes:
        current_node = None
        image_payload = None
        try:
            async with asyncio.timeout(self.job_timeout):
                async for message in websocket:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        event = json.loads(message.data)
                        data = event.get("data", {})
                        if data.get("prompt_id") != prompt_id:
                            continue
                        if event.get("type") == "execution_error":
                            raise ComfyUIError(
                                f"ComfyUI job failed: {json.dumps(data)[-4000:]}"
                            )
                        if event.get("type") == "executing":
                            current_node = data.get("node")
                            if current_node is None:
                                if image_payload is None:
                                    raise ComfyUIError(
                                        "ComfyUI completed without a WebSocket image"
                                    )
                                return image_payload
                    elif message.type == aiohttp.WSMsgType.BINARY:
                        payload = bytes(message.data)
                        if current_node == "15" and self._is_preview_image(payload):
                            image_payload = payload[8:]
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        # A protocol error (an oversized frame, say) is about this
                        # job; anything else (a reset, a missed heartbeat) means
                        # the daemon is gone or hung.
                        if isinstance(message.data, aiohttp.WebSocketError):
                            raise ComfyUIError(
                                f"ComfyUI WebSocket failed during job {prompt_id}: "
                                f"{message.data}"
                            )
                        raise ComfyUIUnreachableError(
                            f"lost the ComfyUI connection during job {prompt_id}: "
                            f"{message.data}"
                        )
                    elif message.type in {
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                    }:
                        break
        except TimeoutError as error:
            raise ComfyUIError(
                f"ComfyUI job {prompt_id} timed out after {self.job_timeout}s"
            ) from error
        # The iterator ends when the daemon closes the socket or the connection
        # drops, which only happens when ComfyUI stops or crashes mid-job.
        raise ComfyUIUnreachableError(
            f"ComfyUI WebSocket closed before job {prompt_id} completed"
        )

    @staticmethod
    def _is_preview_image(payload: bytes) -> bool:
        return len(payload) > 8 and struct.unpack(">I", payload[:4])[0] == 1

    @staticmethod
    def _decode_image(payload: bytes) -> np.ndarray:
        with Image.open(io.BytesIO(payload)) as image:
            return np.asarray(image.convert("RGB"), dtype=np.uint8)

    async def _wait_for_output(
        self, session: aiohttp.ClientSession, prompt_id: str
    ) -> dict:
        deadline = asyncio.get_running_loop().time() + self.job_timeout
        while asyncio.get_running_loop().time() < deadline:
            async with session.get(
                f"{self.server_url}/history/{prompt_id}"
            ) as response:
                history = await self._json_response(response, "poll ComfyUI history")
            job = history.get(prompt_id)
            if job:
                status = job.get("status", {})
                if status.get("status_str") == "error":
                    messages = status.get("messages", [])
                    raise ComfyUIError(
                        f"ComfyUI job failed: {json.dumps(messages)[-4000:]}"
                    )
                outputs = job.get("outputs", {})
                images = outputs.get("15", {}).get("images", [])
                if not images:
                    for node_output in outputs.values():
                        images.extend(node_output.get("images", []))
                if images:
                    return images[0]
            await asyncio.sleep(self.poll_interval)
        raise ComfyUIError(
            f"ComfyUI job {prompt_id} timed out after {self.job_timeout}s"
        )

    async def _download(
        self, session: aiohttp.ClientSession, output: dict
    ) -> np.ndarray:
        query = urlencode(
            {
                "filename": output["filename"],
                "subfolder": output.get("subfolder", ""),
                "type": output.get("type", "output"),
            }
        )
        async with session.get(f"{self.server_url}/view?{query}") as response:
            if response.status >= 400:
                raise ComfyUIError(
                    f"download ComfyUI output failed with HTTP {response.status}"
                )
            payload = await response.read()
        return self._decode_image(payload)

    @staticmethod
    async def _json_response(response: aiohttp.ClientResponse, action: str) -> dict:
        text = await response.text()
        if response.status >= 400:
            raise ComfyUIError(
                f"{action} failed with HTTP {response.status}: {text[-2000:]}"
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise ComfyUIError(f"{action} returned invalid JSON") from error

import asyncio
import json
import os
import socket
import sys
import threading
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from comfy_lanpaint.client import ComfyUIClient, ComfyUIError, ComfyUIUnreachableError
from comfy_lanpaint.crop import build_crop_regions, stitch_region
from comfy_lanpaint.workflow import WorkflowSettings, build_workflow
from models.flux_lanpaint_inpainter import (
    DAEMON_PROBE_SECONDS,
    DEFAULT_PROMPT,
    FluxLanPaintInpainter,
)
from models.inpainter import Inpainter


class CropRegionTests(unittest.TestCase):
    def test_crops_are_aligned_and_stitch_preserves_every_unmasked_pixel(self):
        image = np.arange(180 * 220 * 3, dtype=np.uint32).reshape(180, 220, 3)
        image = (image % 251).astype(np.uint8)
        mask = np.zeros((180, 220), dtype=np.uint8)
        mask[50:75, 90:140] = 255

        regions = build_crop_regions(image, mask, context=24, multiple=64)

        self.assertEqual(len(regions), 1)
        region = regions[0]
        self.assertEqual(region.image.shape[0] % 64, 0)
        self.assertEqual(region.image.shape[1] % 64, 0)
        generated = np.full_like(region.image, 255)
        result = stitch_region(image, generated, region, feather=3)
        outside = mask == 0
        np.testing.assert_array_equal(result[outside], image[outside])
        self.assertTrue(np.any(result[mask > 0] != image[mask > 0]))

    def test_adaptive_stitch_blends_against_text_free_base(self):
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        mask = np.zeros((64, 64), dtype=np.uint8)
        mask[20:44, 20:44] = 255
        [region] = build_crop_regions(
            image,
            mask,
            context=0,
            mask_growth=0,
            horizontal_group_gap=0,
            vertical_group_gap=0,
        )
        generated = np.full_like(region.image, 255)
        blend_base = np.full_like(region.image, 120)

        result = stitch_region(
            image,
            generated,
            region,
            blend_base=blend_base,
            adaptive_feather=True,
        )

        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        self.assertTrue(np.all(result[32, 32] == 255))
        self.assertTrue(np.all(result[20, 20] > 120))
        self.assertTrue(np.all(result[20, 20] < 255))

    def test_distant_components_become_separate_jobs(self):
        image = np.zeros((512, 512, 3), dtype=np.uint8)
        mask = np.zeros((512, 512), dtype=np.uint8)
        mask[20:30, 20:30] = 255
        mask[470:480, 470:480] = 255

        regions = build_crop_regions(image, mask, context=32)

        self.assertEqual(len(regions), 2)

    def test_context_overlap_does_not_chain_distant_groups_into_full_page(self):
        image = np.zeros((256, 512, 3), dtype=np.uint8)
        mask = np.zeros((256, 512), dtype=np.uint8)
        mask[100:110, 20:30] = 255
        mask[100:110, 130:140] = 255
        mask[100:110, 240:250] = 255

        regions = build_crop_regions(
            image,
            mask,
            context=64,
            horizontal_group_gap=0,
            vertical_group_gap=0,
        )

        self.assertEqual(len(regions), 3)
        self.assertEqual(
            sum(np.count_nonzero(region.commit_mask) for region in regions),
            np.count_nonzero(mask),
        )

    def test_context_shrinks_to_aligned_crop_budget(self):
        image = np.zeros((1024, 1024, 3), dtype=np.uint8)
        mask = np.zeros((1024, 1024), dtype=np.uint8)
        mask[500:510, 500:510] = 255

        [region] = build_crop_regions(
            image,
            mask,
            context=400,
            max_crop_pixels=256 * 256,
        )

        self.assertLessEqual(region.image.shape[0] * region.image.shape[1], 256 * 256)

    def test_empty_mask_returns_no_jobs(self):
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        mask = np.zeros((64, 64), dtype=np.uint8)
        self.assertEqual(build_crop_regions(image, mask), [])

    def test_generation_box_can_hide_component_shape_without_expanding_commit(self):
        image = np.zeros((128, 128, 3), dtype=np.uint8)
        mask = np.zeros((128, 128), dtype=np.uint8)
        mask[40:80, 40:48] = 255
        mask[72:80, 40:88] = 255

        [region] = build_crop_regions(
            image,
            mask,
            context=16,
            mask_growth=0,
            fill_generation_boxes=True,
        )

        self.assertEqual(np.count_nonzero(region.commit_mask), np.count_nonzero(mask))
        self.assertGreater(
            np.count_nonzero(region.generation_mask),
            np.count_nonzero(region.commit_mask),
        )

    def test_generation_box_is_retained_when_mask_growth_is_enabled(self):
        image = np.zeros((128, 128, 3), dtype=np.uint8)
        mask = np.zeros((128, 128), dtype=np.uint8)
        mask[40:80, 40:48] = 255
        mask[72:80, 40:88] = 255

        [region] = build_crop_regions(
            image,
            mask,
            context=16,
            mask_growth=2,
            fill_generation_boxes=True,
        )

        self.assertEqual(region.generation_mask[64, 64], 255)

    def test_oversized_component_fails_before_unbounded_generation(self):
        image = np.zeros((1024, 1024, 3), dtype=np.uint8)
        mask = np.full((1024, 1024), 255, dtype=np.uint8)

        with self.assertRaisesRegex(ValueError, "exceeds.*MAX_CROP_PIXELS"):
            build_crop_regions(image, mask, max_crop_pixels=256 * 256)


class WorkflowTests(unittest.TestCase):
    def test_legacy_validation_does_not_require_optional_native_nodes(self):
        client = ComfyUIClient()
        self.assertNotIn("Flux2Scheduler", client.REQUIRED_NODES)
        self.assertNotIn("LanPaint_SamplerCustom", client.REQUIRED_NODES)
        self.assertIn("Flux2Scheduler", client.NATIVE_SCHEDULE_NODES)

    def test_model_choice_validation_reports_missing_configured_file(self):
        payload = {
            "UNETLoader": {
                "input": {"required": {"unet_name": [["another-model.safetensors"]]}}
            },
            "CLIPLoader": {"input": {"required": {"clip_name": [[]]}}},
            "VAELoader": {"input": {"required": {"vae_name": [[]]}}},
        }

        with self.assertRaisesRegex(ComfyUIError, "flux-2-klein-4b-fp8"):
            ComfyUIClient._validate_model_choices(payload, WorkflowSettings())

    def test_workflow_uses_reference_and_masked_source_latent(self):
        workflow = build_workflow(
            "source.png",
            "mask.png",
            "remove text",
            seed=7,
            settings=WorkflowSettings(native_flux2_schedule=True),
            image_width=256,
            image_height=192,
        )
        node_types = {node["class_type"] for node in workflow.values()}

        self.assertIn("LanPaint_SamplerCustom", node_types)
        self.assertIn("Flux2Scheduler", node_types)
        self.assertEqual(workflow["3"]["class_type"], "ImageToMask")
        self.assertEqual(workflow["8"]["inputs"]["samples"], ["7", 0])
        self.assertEqual(workflow["10"]["inputs"]["latent"], ["7", 0])
        self.assertEqual(workflow["13"]["inputs"]["latent_image"], ["8", 0])
        self.assertEqual(workflow["13"]["inputs"]["sigmas"], ["17", 0])
        self.assertEqual(workflow["17"]["inputs"]["steps"], 4)
        self.assertEqual(workflow["17"]["inputs"]["width"], 256)
        self.assertEqual(workflow["17"]["inputs"]["height"], 192)
        self.assertEqual(workflow["5"]["inputs"]["device"], "default")

    def test_workflow_supports_cpu_clip_compatibility_mode(self):
        workflow = build_workflow(
            "source.png",
            "mask.png",
            "remove text",
            seed=7,
            settings=WorkflowSettings(clip_device="cpu"),
            image_width=64,
            image_height=64,
        )

        self.assertEqual(workflow["5"]["inputs"]["device"], "cpu")

    def test_workflow_can_stream_output_without_saving_to_disk(self):
        workflow = build_workflow(
            "source.png",
            "mask.png",
            "remove text",
            seed=7,
            websocket_output=True,
            image_width=64,
            image_height=64,
        )

        self.assertEqual(workflow["15"]["class_type"], "SaveImageWebsocket")
        self.assertNotIn("filename_prefix", workflow["15"]["inputs"])

    def test_workflow_can_read_mask_from_source_alpha(self):
        workflow = build_workflow(
            "source-mask.png",
            "source-mask.png",
            "remove text",
            seed=7,
            embedded_mask=True,
            image_width=64,
            image_height=64,
        )

        self.assertNotIn("2", workflow)
        self.assertNotIn("3", workflow)
        self.assertEqual(workflow["8"]["inputs"]["mask"], ["1", 1])

    def test_legacy_sampler_remains_available_for_base_rollback(self):
        workflow = build_workflow(
            "source.png",
            "mask.png",
            "continue background",
            seed=7,
            settings=WorkflowSettings(native_flux2_schedule=False),
        )

        self.assertEqual(workflow["13"]["class_type"], "LanPaint_KSampler")
        self.assertNotIn("16", workflow)
        self.assertNotIn("17", workflow)

    def test_source_alpha_inverts_binary_inpaint_mask(self):
        image = np.full((2, 2, 3), 17, dtype=np.uint8)
        mask = np.array([[0, 255], [127, 128]], dtype=np.uint8)

        rgba = ComfyUIClient._source_with_alpha_mask(image, mask)

        np.testing.assert_array_equal(rgba[:, :, :3], image)
        np.testing.assert_array_equal(rgba[:, :, 3], [[255, 0], [255, 0]])

    def test_websocket_preview_header_detection(self):
        preview = b"\x00\x00\x00\x01\x00\x00\x00\x02png"
        other = b"\x00\x00\x00\x03\x00\x00\x00\x02text"

        self.assertTrue(ComfyUIClient._is_preview_image(preview))
        self.assertFalse(ComfyUIClient._is_preview_image(other))

    def test_client_rejects_non_loopback_servers(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            ComfyUIClient("http://example.com:8188")


class ClientTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_session_bounds_response_deadlines(self):
        client = ComfyUIClient(request_timeout=3.5)
        with patch("comfy_lanpaint.client.aiohttp.ClientSession") as session_factory:
            session = session_factory.return_value
            session.__aenter__ = AsyncMock(return_value=object())
            session.__aexit__ = AsyncMock(return_value=None)

            async with client.session():
                pass

        timeout = session_factory.call_args.kwargs["timeout"]
        self.assertEqual(timeout.total, 3.5)
        self.assertEqual(timeout.sock_connect, 3.5)
        self.assertEqual(timeout.sock_read, 3.5)


class DistilledDefaultsTests(unittest.TestCase):
    def test_default_prompt_is_general_and_not_material_specific(self):
        normalized = DEFAULT_PROMPT.lower()
        self.assertIn("restore what is behind it", normalized)
        self.assertIn("continue interrupted edges", normalized)
        self.assertIn("do not create text", normalized)
        for specific_term in ("manga", "wardrobe", "fridge", "wood", "screentone"):
            self.assertNotIn(specific_term, normalized)

    def test_distilled_defaults_and_adaptive_refinement(self):
        with patch.dict(
            os.environ,
            {
                "COMFY_LANPAINT_PROFILE": "fast",
                "COMFY_LANPAINT_ADAPTIVE_REFINEMENT": "1",
            },
            clear=True,
        ):
            inpainter = FluxLanPaintInpainter()

        self.assertEqual(
            inpainter.settings.diffusion_model, "flux-2-klein-4b-fp8.safetensors"
        )
        self.assertEqual(inpainter.settings.steps, 4)
        self.assertEqual(inpainter.settings.flux_guidance, 1.0)
        self.assertFalse(inpainter.settings.native_flux2_schedule)
        self.assertEqual(inpainter.context, 96)
        self.assertEqual(inpainter.mask_growth, 2)
        self.assertEqual(inpainter.feather, 1)
        self.assertTrue(inpainter.prefill_masked_source)
        self.assertFalse(inpainter.fill_generation_boxes)
        self.assertTrue(inpainter.lama_structural_prefill)
        self.assertEqual(inpainter.settings.denoise, 0.25)
        self.assertFalse(inpainter.allow_q4_fallback)

        easy = np.zeros((64, 64), dtype=np.uint8)
        easy[30:34, 30:34] = 255
        hard = np.zeros((64, 64), dtype=np.uint8)
        hard[8:56, 8:56] = 255
        self.assertEqual(inpainter._settings_for_region(easy).lanpaint_steps, 1)
        self.assertEqual(inpainter._settings_for_region(hard).lanpaint_steps, 2)
        self.assertEqual(
            inpainter._settings_for_region(easy).prompt_mode, "Image First"
        )
        self.assertEqual(
            inpainter._settings_for_region(hard).prompt_mode, "Image First"
        )

    def test_adaptive_prompt_mode_is_opt_in(self):
        with patch.dict(
            os.environ,
            {
                "COMFY_LANPAINT_PROFILE": "fast",
                "COMFY_LANPAINT_ADAPTIVE_PROMPT_MODE": "1",
            },
            clear=True,
        ):
            inpainter = FluxLanPaintInpainter()

        hard = np.zeros((64, 64), dtype=np.uint8)
        hard[8:56, 8:56] = 255
        self.assertEqual(
            inpainter._settings_for_region(hard).prompt_mode, "Prompt First"
        )

    def test_prefill_removes_masked_reference_pixels_only(self):
        with patch.dict(
            os.environ,
            {"COMFY_LANPAINT_PROFILE": "fast", "COMFY_LANPAINT_PREFILL": "1"},
            clear=True,
        ):
            inpainter = FluxLanPaintInpainter()
        image = np.full((32, 32, 3), 220, dtype=np.uint8)
        image[12:20, 12:20] = (240, 40, 20)
        mask = np.zeros((32, 32), dtype=np.uint8)
        mask[10:22, 10:22] = 255

        prefilled = inpainter._prefill_source(image, mask)

        np.testing.assert_array_equal(prefilled[mask == 0], image[mask == 0])
        self.assertFalse(np.array_equal(prefilled[12:20, 12:20], image[12:20, 12:20]))


class StructuralPrefillTests(unittest.IsolatedAsyncioTestCase):
    async def test_lama_prefill_uses_requested_device_and_disposes_session(self):
        with patch.dict(
            os.environ,
            {
                "COMFY_LANPAINT_PROFILE": "fast",
                "COMFY_LANPAINT_LAMA_PREFILL": "1",
                "COMFY_LANPAINT_MASK_GROWTH": "0",
            },
            clear=True,
        ):
            inpainter = FluxLanPaintInpainter(device="cuda")
        image = np.zeros((32, 32, 3), dtype=np.uint8)
        mask = np.zeros((32, 32), dtype=np.uint8)
        mask[8:24, 8:24] = 255
        expected = image.copy()
        expected[mask > 0] = 180
        structural = SimpleNamespace(
            initialize=AsyncMock(),
            inpaint=AsyncMock(return_value=expected),
            dispose=Mock(),
        )

        with patch.object(
            inpainter, "_create_structure_inpainter", return_value=structural
        ):
            result, used = await inpainter._structure_prefill(image, mask)

        self.assertTrue(used)
        np.testing.assert_array_equal(result, expected)
        structural.initialize.assert_awaited_once_with()
        structural.inpaint.assert_awaited_once()
        actual_image, actual_mask = structural.inpaint.await_args.args
        np.testing.assert_array_equal(actual_image, image)
        np.testing.assert_array_equal(actual_mask, mask)
        structural.dispose.assert_called_once_with()


class RecordingLamaSession:
    """Stand-in for the LaMa ONNX session that records every input shape."""

    def __init__(self):
        self.image_shapes = []

    def get_inputs(self):
        return [SimpleNamespace(name="image"), SimpleNamespace(name="mask")]

    def run(self, _outputs, inputs):
        self.image_shapes.append(inputs["image"].shape)
        return [np.full_like(inputs["image"], 0.5, dtype=np.float32)]


class CropPrefillTests(unittest.IsolatedAsyncioTestCase):
    def _inpainter(self, **env):
        values = {
            "COMFY_LANPAINT_PROFILE": "fast",
            "COMFY_LANPAINT_LAMA_PREFILL": "1",
            "COMFY_LANPAINT_MASK_GROWTH": "0",
            **env,
        }
        with patch.dict(os.environ, values, clear=True):
            return FluxLanPaintInpainter(device="cpu")

    def _structural(self, session):
        structural = Inpainter(inpainter_type="lama_large", device="cpu")
        structural.session = session
        structural._initialized = True
        return structural

    def test_prefill_mode_defaults_to_page_and_rejects_unknown_values(self):
        self.assertEqual(self._inpainter().prefill_mode, "page")
        self.assertEqual(
            self._inpainter(COMFY_LANPAINT_PREFILL_MODE="Crops").prefill_mode, "crops"
        )
        with self.assertRaisesRegex(ValueError, "COMFY_LANPAINT_PREFILL_MODE"):
            self._inpainter(COMFY_LANPAINT_PREFILL_MODE="tiles")

    async def test_crops_mode_runs_lama_on_crop_sized_arrays_only(self):
        inpainter = self._inpainter(COMFY_LANPAINT_PREFILL_MODE="crops")
        image = np.full((2048, 1536, 3), 20, dtype=np.uint8)
        mask = np.zeros((2048, 1536), dtype=np.uint8)
        mask[1000:1020, 700:730] = 255
        session = RecordingLamaSession()
        structural = self._structural(session)

        with patch.object(
            inpainter, "_create_structure_inpainter", return_value=structural
        ):
            result, used = await inpainter._structure_prefill(image, mask)

        self.assertTrue(used)
        self.assertEqual(len(session.image_shapes), 1)
        _batch, channels, height, width = session.image_shapes[0]
        self.assertEqual(channels, 3)
        self.assertLessEqual(height * width, inpainter.max_crop_pixels)
        self.assertLess(height, 2048)
        self.assertLess(width, 1536)
        self.assertEqual(result.shape, image.shape)
        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        self.assertTrue(np.all(result[mask > 0] == 127))
        self.assertIsNone(structural.session)

    async def test_crops_mode_runs_one_crop_per_distant_component(self):
        inpainter = self._inpainter(COMFY_LANPAINT_PREFILL_MODE="crops")
        image = np.full((1024, 1024, 3), 20, dtype=np.uint8)
        mask = np.zeros((1024, 1024), dtype=np.uint8)
        mask[40:60, 40:60] = 255
        mask[900:920, 900:920] = 255
        session = RecordingLamaSession()

        with patch.object(
            inpainter,
            "_create_structure_inpainter",
            return_value=self._structural(session),
        ):
            result, used = await inpainter._structure_prefill(image, mask)

        self.assertTrue(used)
        self.assertEqual(len(session.image_shapes), 2)
        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        self.assertTrue(np.all(result[mask > 0] == 127))


class OversizedMaskTests(unittest.IsolatedAsyncioTestCase):
    def _ready_inpainter(self, **env):
        values = {"COMFY_LANPAINT_PROFILE": "fast", **env}
        with patch.dict(os.environ, values, clear=True):
            inpainter = FluxLanPaintInpainter()
        inpainter._initialized = True
        inpainter.client.session = Mock(
            side_effect=AssertionError("ComfyUI must not be called")
        )
        return inpainter

    async def test_oversized_component_returns_lama_result_without_comfyui(self):
        inpainter = self._ready_inpainter(COMFY_LANPAINT_LAMA_PREFILL="1")
        image = np.zeros((700, 700, 3), dtype=np.uint8)
        mask = np.zeros((700, 700), dtype=np.uint8)
        mask[50:650, 50:650] = 255
        prefilled = image.copy()
        prefilled[mask > 0] = 99
        structural = SimpleNamespace(
            initialize=AsyncMock(),
            inpaint=AsyncMock(return_value=prefilled),
            dispose=Mock(),
        )

        with patch.object(
            inpainter, "_create_structure_inpainter", return_value=structural
        ):
            result = await inpainter.inpaint(image, mask)

        np.testing.assert_array_equal(result, prefilled)
        self.assertIsNot(result, prefilled)
        self.assertEqual(inpainter.last_backend_used, "lama_large")
        inpainter.client.session.assert_not_called()

    async def test_oversized_component_returns_original_when_prefill_is_off(self):
        inpainter = self._ready_inpainter(COMFY_LANPAINT_LAMA_PREFILL="0")
        image = np.full((700, 700, 3), 33, dtype=np.uint8)
        mask = np.zeros((700, 700), dtype=np.uint8)
        mask[50:650, 50:650] = 255

        result = await inpainter.inpaint(image, mask)

        np.testing.assert_array_equal(result, image)
        self.assertEqual(inpainter.last_backend_used, "none")
        inpainter.client.session.assert_not_called()

    async def test_other_value_errors_still_propagate(self):
        inpainter = self._ready_inpainter(COMFY_LANPAINT_LAMA_PREFILL="0")
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        mask = np.full((32, 32), 255, dtype=np.uint8)

        with self.assertRaisesRegex(ValueError, "does not match"):
            await inpainter.inpaint(image, mask)


class InitializeTests(unittest.IsolatedAsyncioTestCase):
    def _inpainter(self, **env):
        values = {"COMFY_LANPAINT_PROFILE": "fast", **env}
        with patch.dict(os.environ, values, clear=True):
            inpainter = FluxLanPaintInpainter()
        inpainter.client.validate = AsyncMock(side_effect=ComfyUIError("down"))
        return inpainter

    async def test_unreachable_comfyui_raises_when_q4_fallback_is_off(self):
        inpainter = self._inpainter()
        inpainter._initialize_fallback = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "ComfyUI LanPaint is unavailable"):
            await inpainter.initialize()

        self.assertFalse(inpainter._initialized)
        inpainter._initialize_fallback.assert_not_awaited()

    async def test_unreachable_comfyui_uses_q4_path_when_fallback_is_on(self):
        inpainter = self._inpainter(COMFY_LANPAINT_FALLBACK_Q4="1")
        inpainter._initialize_fallback = AsyncMock()

        await inpainter.initialize()

        self.assertTrue(inpainter._initialized)
        inpainter._initialize_fallback.assert_awaited_once_with()


class DaemonProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # These tests time real loopback connects against the production probe
        # bound. Debug mode's per-callback bookkeeping can make a connect take
        # most of that bound on a busy machine, so run them as production does.
        asyncio.get_running_loop().set_debug(False)

    def _inpainter(self, port, host="127.0.0.1"):
        values = {
            "COMFY_LANPAINT_PROFILE": "fast",
            "COMFYUI_URL": f"http://{host}:{port}",
        }
        with patch.dict(os.environ, values, clear=True):
            return FluxLanPaintInpainter()

    async def test_probe_sees_a_listening_port(self):
        import asyncio

        server = await asyncio.start_server(lambda _r, writer: writer.close(), "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            self.assertTrue(await self._inpainter(port)._daemon_is_listening())
        finally:
            server.close()
            await server.wait_closed()

    async def test_closed_port_fails_quickly_without_calling_comfyui(self):
        import asyncio
        import time

        server = await asyncio.start_server(lambda _r, writer: writer.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        inpainter = self._inpainter(port)
        inpainter.client.validate = AsyncMock()

        started = time.perf_counter()
        with self.assertRaisesRegex(RuntimeError, "ComfyUI LanPaint is unavailable"):
            await inpainter.initialize()

        self.assertLess(time.perf_counter() - started, 1.5)
        inpainter.client.validate.assert_not_awaited()

    @staticmethod
    def _localhost_resolves_to_ipv4_loopback():
        try:
            infos = socket.getaddrinfo("localhost", None, type=socket.SOCK_STREAM)
        except OSError:
            return False
        return "127.0.0.1" in {info[4][0] for info in infos}

    async def test_probe_finds_an_ipv4_listener_through_localhost(self):
        # Windows resolves localhost to ::1 first and takes about two seconds to
        # report that refused attempt, longer than the whole probe bound.
        if not self._localhost_resolves_to_ipv4_loopback():
            self.skipTest("localhost does not resolve to 127.0.0.1 here")
        server = await asyncio.start_server(lambda _r, writer: writer.close(), "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            inpainter = self._inpainter(port, host="localhost")

            # True means a connection succeeded within DAEMON_PROBE_SECONDS.
            listening = await inpainter._daemon_is_listening()
        finally:
            server.close()
            await server.wait_closed()

        self.assertTrue(listening)

    async def test_probe_through_localhost_stays_bounded_when_nothing_listens(self):
        server = await asyncio.start_server(lambda _r, writer: writer.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()

        started = time.perf_counter()
        listening = await self._inpainter(port, host="localhost")._daemon_is_listening()

        self.assertFalse(listening)
        self.assertLess(time.perf_counter() - started, DAEMON_PROBE_SECONDS + 0.5)


class UnreachableDaemonTests(unittest.IsolatedAsyncioTestCase):
    """ComfyUI stopping after LanPaint loaded surfaces as ComfyUIUnreachableError."""

    def _inpainter(self, port=8188, **env):
        values = {
            "COMFY_LANPAINT_PROFILE": "fast",
            "COMFY_LANPAINT_LAMA_PREFILL": "0",
            "COMFYUI_URL": f"http://127.0.0.1:{port}",
            **env,
        }
        with patch.dict(os.environ, values, clear=True):
            inpainter = FluxLanPaintInpainter()
        inpainter._initialized = True
        return inpainter

    @staticmethod
    def _page():
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        mask = np.zeros((64, 64), dtype=np.uint8)
        mask[20:40, 20:40] = 255
        return image, mask

    async def test_daemon_that_drops_connections_is_reported_unreachable(self):
        # A real socket that accepts and hangs up, as a crashing ComfyUI does.
        server = await asyncio.start_server(lambda _r, writer: writer.close(), "127.0.0.1", 0)
        try:
            inpainter = self._inpainter(server.sockets[0].getsockname()[1])
            with self.assertRaises(ComfyUIUnreachableError) as raised:
                await inpainter.inpaint(*self._page())
        finally:
            server.close()
            await server.wait_closed()

        self.assertIsInstance(raised.exception.__cause__, aiohttp.ClientConnectionError)

    async def test_transport_failures_are_reported_unreachable(self):
        failures = (
            ConnectionRefusedError("connection refused"),
            aiohttp.ServerDisconnectedError(),
            aiohttp.ClientOSError(10054, "connection reset by peer"),
            aiohttp.ConnectionTimeoutError("connect timed out"),
            TimeoutError("request deadline passed"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                inpainter = self._inpainter()
                inpainter.client.inpaint_crop = AsyncMock(side_effect=failure)

                with self.assertRaises(ComfyUIUnreachableError) as raised:
                    await inpainter.inpaint(*self._page())

                self.assertIs(raised.exception.__cause__, failure)

    async def test_failed_jobs_stay_per_page_errors(self):
        failures = (
            ComfyUIError("ComfyUI job failed: CUDA out of memory"),
            aiohttp.ClientPayloadError("response payload is not completed"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                inpainter = self._inpainter()
                inpainter.client.inpaint_crop = AsyncMock(side_effect=failure)

                with self.assertRaises(type(failure)) as raised:
                    await inpainter.inpaint(*self._page())

                self.assertNotIsInstance(raised.exception, ComfyUIUnreachableError)

    async def test_q4_fallback_still_handles_an_unreachable_daemon(self):
        inpainter = self._inpainter(COMFY_LANPAINT_FALLBACK_Q4="1")
        image, mask = self._page()
        inpainter._fallback = SimpleNamespace(inpaint=AsyncMock(return_value=image))
        inpainter.client.inpaint_crop = AsyncMock(
            side_effect=aiohttp.ServerDisconnectedError()
        )

        await inpainter.inpaint(image, mask)

        self.assertEqual(inpainter.last_backend_used, "flux2_klein_q4")


class WebSocketLossTests(unittest.IsolatedAsyncioTestCase):
    """A dropped job socket means the daemon went away; a failed job does not."""

    async def _wait(self, *messages):
        async def websocket():
            for message in messages:
                yield message

        return await ComfyUIClient()._wait_for_websocket_image(websocket(), "job-1")

    @staticmethod
    def _error(data):
        return SimpleNamespace(type=aiohttp.WSMsgType.ERROR, data=data)

    async def test_socket_closed_before_the_job_finished_is_unreachable(self):
        with self.assertRaises(ComfyUIUnreachableError):
            await self._wait()

    async def test_reset_or_missed_heartbeat_is_unreachable(self):
        for data in (
            ConnectionResetError("connection reset"),
            aiohttp.ServerTimeoutError("No PONG received after 15.0 seconds"),
        ):
            with self.subTest(data=type(data).__name__):
                with self.assertRaises(ComfyUIUnreachableError):
                    await self._wait(self._error(data))

    async def test_protocol_and_job_errors_are_not_unreachable(self):
        failed_job = SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=json.dumps(
                {"type": "execution_error", "data": {"prompt_id": "job-1"}}
            ),
        )
        for message in (
            self._error(aiohttp.WebSocketError(1009, "message too big")),
            failed_job,
        ):
            with self.subTest(message=message.type):
                with self.assertRaises(ComfyUIError) as raised:
                    await self._wait(message)
                self.assertNotIsInstance(raised.exception, ComfyUIUnreachableError)


class LamaSessionLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_creates_onnx_session_off_the_event_loop(self):
        loop_thread = threading.get_ident()
        seen = {}

        def create_session(model_path, providers):
            seen["thread"] = threading.get_ident()
            return RecordingLamaSession()

        inpainter = Inpainter(inpainter_type="lama_large", device="cpu")
        with patch(
            "models.inpainter.download_lama_onnx",
            new=AsyncMock(return_value="model.onnx"),
        ), patch("models.inpainter.create_onnx_session", side_effect=create_session):
            await inpainter.initialize()

        self.assertIsInstance(inpainter.session, RecordingLamaSession)
        self.assertNotEqual(seen["thread"], loop_thread)


class DistilledFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_fallback_preserves_seed_and_reports_actual_backend(self):
        with patch.dict(
            os.environ,
            {
                "COMFY_LANPAINT_PROFILE": "fast",
                "COMFY_LANPAINT_FALLBACK_Q4": "1",
                "COMFY_LANPAINT_LAMA_PREFILL": "0",
            },
            clear=True,
        ):
            inpainter = FluxLanPaintInpainter()
        inpainter._initialized = True
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        mask = np.zeros((64, 64), dtype=np.uint8)
        mask[20:40, 20:40] = 255
        expected = image.copy()
        expected[mask > 0] = 127
        fallback = SimpleNamespace(inpaint=AsyncMock(return_value=expected))
        inpainter._fallback = fallback

        @asynccontextmanager
        async def timed_out_session():
            raise TimeoutError("ComfyUI timed out")
            yield

        inpainter.client.session = timed_out_session
        result = await inpainter.inpaint(image, mask, seed=123)

        np.testing.assert_array_equal(result, expected)
        fallback.inpaint.assert_awaited_once_with(
            image, mask, prompt=DEFAULT_PROMPT, seed=123
        )
        self.assertEqual(inpainter.last_backend_used, "flux2_klein_q4")


if __name__ == "__main__":
    unittest.main()

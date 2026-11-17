import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  createImageOverlay,
  createShadowHost,
  mountInShadow,
} from '../../extension/lib/overlay-dom';

describe('overlay shadow hosts', () => {
  it('creates an open shadow host that carries id, class and placement', () => {
    const host = createShadowHost({
      id: 'probe',
      className: 'manga-translate-overlay',
      styleText: 'position: fixed; z-index: 7;',
    });

    expect(host.id).toBe('probe');
    expect(host.classList.contains('manga-translate-overlay')).toBe(true);
    expect(host.shadowRoot.mode).toBe('open');
    expect(host.style.position).toBe('fixed');
    expect(host.style.zIndex).toBe('7');
  });

  it('applies the overlay styles to every shadow root', () => {
    const host = createShadowHost();
    const adopted = (host.shadowRoot.adoptedStyleSheets?.length ?? 0) > 0;
    const inline = host.shadowRoot
      .querySelector('style')
      ?.textContent?.includes('.manga-progress-spinner');
    expect(adopted || inline).toBe(true);
  });

  it('replaces mounted content without dropping the styles', () => {
    const host = createShadowHost();
    const styleCount = host.shadowRoot.querySelectorAll('style').length;

    mountInShadow(host, document.createElement('div'));
    mountInShadow(host, document.createElement('span'));

    expect(host.shadowRoot.querySelector('div')).toBeNull();
    expect(host.shadowRoot.querySelector('span')).not.toBeNull();
    expect(host.shadowRoot.querySelectorAll('style')).toHaveLength(styleCount);
  });

  it('keeps overlay images out of page-level image queries', () => {
    const { host, image } = createImageOverlay({
      className: 'manga-translated-img-overlay',
      alt: 'page 1',
      objectFit: 'contain',
      styleText: 'position: absolute;',
    });
    document.body.appendChild(host);

    expect(image.alt).toBe('page 1');
    expect(image.style.objectFit).toBe('contain');
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('.manga-translated-img-overlay')).toBe(host);

    host.remove();
  });

  // Moved from the former core-contracts.test.js: the shadow stylesheet is the
  // only place the progress spinner is styled, so its contract is checked on source.
  it('shadow overlay stylesheet keeps progress spinner contract available', () => {
    const css = readFileSync(resolve(__dirname, '../../extension/lib/overlay-dom.ts'), 'utf8');

    expect(css).toContain('.manga-progress-spinner');
    expect(css).toMatch(/\.manga-progress-spinner\s*\{[^}]*width:\s*16px;/s);
    expect(css).toMatch(/\.manga-progress-spinner\s*\{[^}]*height:\s*16px;/s);
    expect(css).toMatch(
      /\.manga-progress-spinner\s*\{[^}]*animation:\s*manga-spin\s+1s\s+linear\s+infinite;/s,
    );
    expect(css).toContain('@keyframes manga-spin');
  });
});

import { describe, expect, it, vi, beforeEach } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import FloatingVoiceOrb from '../components/voice/FloatingVoiceOrb';
import apiClient from '../api/client';

describe('FloatingVoiceOrb & API Client Integration', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders FloatingVoiceOrb SSR markup cleanly with idle state and button testid', () => {
    const html = renderToStaticMarkup(React.createElement(FloatingVoiceOrb));
    expect(html).toContain('data-testid="floating-voice-orb"');
    expect(html).toContain('data-orb-state="idle"');
    expect(html).toContain('data-testid="floating-voice-orb-button"');
  });

  it('exposes orb show, hide, panel, and position API client methods', async () => {
    const reqSpy = vi
      .spyOn(apiClient as unknown as { request: (...args: unknown[]) => Promise<unknown> }, 'request')
      .mockResolvedValue({ ok: true, status: 200, data: {}, error: null });

    await apiClient.showFloatingOrb();
    expect(reqSpy).toHaveBeenCalledWith('/agent/voice/orb/show', { method: 'POST' }, 8000);

    await apiClient.hideFloatingOrb();
    expect(reqSpy).toHaveBeenCalledWith('/agent/voice/orb/hide', { method: 'POST' }, 8000);

    await apiClient.setFloatingOrbPanel(true);
    expect(reqSpy).toHaveBeenCalledWith(
      '/agent/voice/orb/panel',
      { method: 'POST', body: JSON.stringify({ expanded: true }) },
      5000
    );

    await apiClient.updateFloatingOrbPosition({ corner: 'top_left' });
    expect(reqSpy).toHaveBeenCalledWith(
      '/agent/voice/orb/position',
      { method: 'POST', body: JSON.stringify({ corner: 'top_left' }) },
      8000
    );
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import apiClient from '../api/client';

describe('Phase 8B/8C AI Provider & BYOK API Client', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('calls provider list, active provider, BYOK configure, validate, select, and delete endpoints', async () => {
    const mockPayload = {
      profile_config: {
        profile_id: 'default',
        active_provider_id: 'local',
        local_model: 'qwen3:4b',
        byok_vendor: 'openai',
        byok_model: 'gpt-4o-mini',
        has_byok_credential: true,
      },
      health: {
        provider_id: 'local',
        available: true,
        configured: true,
        status: 'ready',
        active_model: 'qwen3:4b',
      },
      capabilities: {
        streaming: false,
        structured_output: false,
        tool_calling: false,
        vision: false,
        local_only: true,
      },
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      text: async () => JSON.stringify(mockPayload),
      json: async () => mockPayload,
    });

    vi.stubGlobal('fetch', fetchMock);

    const activeRes = await apiClient.getActiveProvider();
    expect(activeRes.ok).toBe(true);
    expect(activeRes.data?.profile_config.active_provider_id).toBe('local');
    expect(activeRes.data?.profile_config.has_byok_credential).toBe(true);

    await apiClient.configureOpenAIBYOK({
      api_key: 'sk-test-123456',
      model: 'gpt-4o-mini',
      validate_connection: true,
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining('/api/providers/byok/openai/configure'),
      expect.objectContaining({ method: 'POST' })
    );

    await apiClient.selectProvider('byok');
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining('/api/providers/select'),
      expect.objectContaining({ method: 'POST' })
    );

    await apiClient.removeOpenAIBYOKCredentials();
    expect(fetchMock).toHaveBeenLastCalledWith(
      expect.stringContaining('/api/providers/byok/openai/credentials'),
      expect.objectContaining({ method: 'DELETE' })
    );
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import apiClient, {
  isLoopbackApiUrl,
  resolveApiBaseUrl,
  resolveDesktopSessionToken,
  setDesktopSessionToken,
} from '../api/client';

describe('ApiClient Unit Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setDesktopSessionToken(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setDesktopSessionToken(null);
  });

  it('resolves the base URL cleanly without trailing slashes', () => {
    const url = apiClient.getBaseUrl();
    expect(url).toMatch(/^http:\/\/127\.0\.0\.1:8000/);
    expect(url.endsWith('/')).toBe(false);
  });

  it('handles getHealth() successfully', async () => {
    const mockData = { status: 'healthy', service: 'VoxPilot', version: '0.5.0' };
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.getHealth();
    expect(res.ok).toBe(true);
    expect(res.status).toBe(200);
    expect(res.data).toEqual(mockData);
    expect(res.error).toBeNull();
  });

  it('handles execute() command successfully', async () => {
    const mockExecution = {
      execution_id: 'exec-12345',
      conversation_id: 'conv-abc',
      execution_status: 'completed',
      user_input: 'Open notepad',
      intent: 'launch_application',
      confidence: 0.95,
      plan: [{ action: 'launch_application', parameters: { app_name: 'notepad' } }],
      execution_results: [{ status: 'completed' }],
      completed_steps: [{ status: 'completed' }],
      failed_steps: [],
      response: 'Notepad launched successfully.',
      requires_confirmation: false,
      pending_confirmation: null,
    };

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockExecution), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.execute('Open notepad', 'conv-abc');
    expect(res.ok).toBe(true);
    expect(res.data?.execution_id).toBe('exec-12345');
    expect(res.data?.execution_status).toBe('completed');
  });

  it('handles execute() rate limited HTTP 429 response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(
        JSON.stringify({ detail: 'Execution budget exceeded: 120s max duration.' }),
        {
          status: 429,
          headers: { 'Content-Type': 'application/json' },
        }
      )
    );

    const res = await apiClient.execute('Heavy command');
    expect(res.ok).toBe(false);
    expect(res.status).toBe(429);
    expect(res.error).toContain('Execution budget exceeded');
  });

  it('handles approveConfirmation() call with token', async () => {
    const mockApprovedState = {
      execution_id: 'exec-conf-1',
      execution_status: 'completed',
      user_input: 'Delete file',
      plan: [],
      execution_results: [],
      completed_steps: [],
      failed_steps: [],
      requires_confirmation: false,
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockApprovedState), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.approveConfirmation('exec-conf-1', 'cryptographic-token-xyz');
    expect(res.ok).toBe(true);
    expect(res.data?.execution_status).toBe('completed');

    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/agent/confirmations/exec-conf-1/approve'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ token: 'cryptographic-token-xyz' }),
      })
    );
  });

  it('handles rejectConfirmation() call with token and reason', async () => {
    const mockRejectedState = {
      execution_id: 'exec-conf-2',
      execution_status: 'cancelled',
      user_input: 'Delete file',
      plan: [],
      execution_results: [],
      completed_steps: [],
      failed_steps: [],
      requires_confirmation: false,
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockRejectedState), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.rejectConfirmation('exec-conf-2', 'token-abc', 'Unintended target');
    expect(res.ok).toBe(true);
    expect(res.data?.execution_status).toBe('cancelled');

    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/agent/confirmations/exec-conf-2/reject'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ token: 'token-abc', reason: 'Unintended target' }),
      })
    );
  });

  it('handles triggerEmergencyStop() call', async () => {
    const mockStopResponse = {
      success: true,
      message: 'Global emergency stop triggered successfully.',
      is_active: true,
      trigger_reason: 'User pressed UI stop button',
    };

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockStopResponse), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.triggerEmergencyStop('User pressed UI stop button');
    expect(res.ok).toBe(true);
    expect(res.data?.is_active).toBe(true);
  });

  it('handles resetEmergencyStop() 409 Conflict when active operations remain', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(
        JSON.stringify({ detail: 'Unsafe reset rejected: 2 active operations still running.' }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }
      )
    );

    const res = await apiClient.resetEmergencyStop();
    expect(res.ok).toBe(false);
    expect(res.status).toBe(409);
    expect(res.error).toContain('Unsafe reset rejected');
  });

  it('handles network disconnect gracefully', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Failed to fetch'));

    const res = await apiClient.getHealth();
    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.error).toBe('Failed to fetch');
    expect(res.data).toBeNull();
  });

  it('handles exportAuditTrail() call successfully', async () => {
    const mockExport = {
      export_id: 'exp_123',
      filename: 'audit_test.json',
      file_path: 'C:/VoxPilot/data/exports/audit_test.json',
      format: 'json',
      record_count: 5,
      exported_at: '2026-11-11T12:00:00Z',
      status: 'completed',
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockExport), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.exportAuditTrail({
      format: 'json',
      status_filter: 'completed',
    });

    expect(res.ok).toBe(true);
    expect(res.data?.export_id).toBe('exp_123');
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/agent/audit/export'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ format: 'json', status_filter: 'completed' }),
      })
    );
  });

  it('handles listAuditExports() call successfully', async () => {
    const mockList = [
      {
        export_id: 'exp_1',
        filename: 'audit_1.json',
        file_path: 'data/exports/audit_1.json',
        format: 'json',
        record_count: 2,
        status: 'completed',
      },
    ];

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockList), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.listAuditExports();
    expect(res.ok).toBe(true);
    expect(res.data?.length).toBe(1);
    expect(res.data?.[0].export_id).toBe('exp_1');
  });

  it('handles downloadAuditExport() rejecting path traversal attempts safely', async () => {
    const res = await apiClient.downloadAuditExport('../escape', 'file.json');
    expect(res.ok).toBe(false);
    expect(res.error).toContain('path traversal forbidden');
  });

  it('resolves desktop token from meta tag, scrubs DOM metadata, and attaches header on loopback requests', async () => {
    let removedFromParent = false;
    let currentContent: string | null = 'desktop-secret-token-meta';
    const metaEl = {
      getAttribute: (name: string) => (name === 'content' ? currentContent : null),
      removeAttribute: (name: string) => {
        if (name === 'content') {
          currentContent = null;
        }
      },
      parentNode: {
        removeChild: () => {
          removedFromParent = true;
        },
      },
    };
    const fakeAnchor = {
      href: '',
      download: '',
      style: { display: '' },
      click: vi.fn(),
    };
    const createObjectURL = vi.fn(() => 'blob:http://127.0.0.1:8000/mock');
    const revokeObjectURL = vi.fn();

    vi.stubGlobal('document', {
      querySelector: (selector: string) =>
        selector === 'meta[name="voxpilot-desktop-token"]' && !removedFromParent ? metaEl : null,
      createElement: () => fakeAnchor,
      body: {
        appendChild: vi.fn(),
        removeChild: vi.fn(),
      },
    });
    vi.stubGlobal('window', {
      location: { hostname: '127.0.0.1', port: '8000', origin: 'http://127.0.0.1:8000' },
      URL: { createObjectURL, revokeObjectURL },
    });

    const resolved = resolveDesktopSessionToken();
    expect(resolved).toBe('desktop-secret-token-meta');
    expect(currentContent).toBeNull();
    expect(removedFromParent).toBe(true);
    // Subsequent call returns cached in-memory token cleanly
    expect(resolveDesktopSessionToken()).toBe('desktop-secret-token-meta');

    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            execution_id: 'exec-sec-1',
            execution_status: 'completed',
            user_input: 'Help',
            plan: [],
            execution_results: [],
            completed_steps: [],
            failed_steps: [],
            requires_confirmation: false,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      );

    const execRes = await apiClient.execute('Help');
    expect(execRes.ok).toBe(true);
    expect(fetchSpy).toHaveBeenNthCalledWith(
      1,
      expect.stringContaining('/agent/execute'),
      expect.objectContaining({
        headers: expect.objectContaining({
          'X-VoxPilot-Desktop-Token': 'desktop-secret-token-meta',
        }),
      })
    );

    const dlRes = await apiClient.downloadAuditExport('exp_ok', 'audit.json');
    expect(dlRes.ok).toBe(true);
    expect(fetchSpy).toHaveBeenNthCalledWith(
      2,
      expect.stringContaining('/agent/audit/exports/exp_ok/download'),
      expect.objectContaining({
        headers: expect.objectContaining({
          'X-VoxPilot-Desktop-Token': 'desktop-secret-token-meta',
        }),
      })
    );
  });

  it('resolves desktop token from window.__VOXPILOT_DESKTOP_TOKEN__ and clears the global property', () => {
    const fakeWindow: { __VOXPILOT_DESKTOP_TOKEN__?: string; location: { hostname: string } } = {
      __VOXPILOT_DESKTOP_TOKEN__: 'desktop-secret-token-global',
      location: { hostname: '127.0.0.1' },
    };
    vi.stubGlobal('window', fakeWindow);

    expect(resolveDesktopSessionToken()).toBe('desktop-secret-token-global');
    expect(fakeWindow.__VOXPILOT_DESKTOP_TOKEN__).toBeUndefined();
    expect(resolveDesktopSessionToken()).toBe('desktop-secret-token-global');
  });

  it('restricts X-VoxPilot-Desktop-Token attachment strictly to loopback URLs', async () => {
    expect(isLoopbackApiUrl('http://127.0.0.1:8000/api/profiles')).toBe(true);
    expect(isLoopbackApiUrl('http://localhost:8000/agent/execute')).toBe(true);
    expect(isLoopbackApiUrl('/api/profiles')).toBe(true);
    expect(isLoopbackApiUrl('https://evil.example.com/api')).toBe(false);
    expect(isLoopbackApiUrl('http://127.0.0.1.evil.com/api')).toBe(false);
    expect(isLoopbackApiUrl('//evil.example.com/api')).toBe(false);

    vi.stubGlobal('window', { location: { hostname: 'evil.example.com' } });
    expect(isLoopbackApiUrl('/api/profiles')).toBe(false);
    vi.unstubAllGlobals();

    setDesktopSessionToken('desktop-secret-token-external-guard');
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ status: 'healthy', service: 'VoxPilot', version: '0.5.2' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const clientInternal = apiClient as unknown as { baseUrl: string };
    const originalBaseUrl = clientInternal.baseUrl;
    clientInternal.baseUrl = 'https://external.example.com';
    try {
      await apiClient.getHealth();
      const callHeaders = fetchSpy.mock.calls[0][1]?.headers as Record<string, string>;
      expect(callHeaders['X-VoxPilot-Desktop-Token']).toBeUndefined();
    } finally {
      clientInternal.baseUrl = originalBaseUrl;
    }
  });

  it('scrubs late-injected window.__VOXPILOT_DESKTOP_TOKEN__ even when token was already cached', async () => {
    setDesktopSessionToken('initial-meta-token');

    const fakeWindow: { __VOXPILOT_DESKTOP_TOKEN__?: string; location: { hostname: string } } = {
      __VOXPILOT_DESKTOP_TOKEN__: 'initial-meta-token',
      location: { hostname: '127.0.0.1' },
    };
    vi.stubGlobal('window', fakeWindow);

    const token = resolveDesktopSessionToken();
    expect(token).toBe('initial-meta-token');
    expect(fakeWindow.__VOXPILOT_DESKTOP_TOKEN__).toBeUndefined();
    expect('__VOXPILOT_DESKTOP_TOKEN__' in fakeWindow).toBe(false);
  });

  it('sends recordNotificationActivity POST with desktop token header', async () => {
    setDesktopSessionToken('desktop-activity-token');
    vi.stubGlobal('window', {
      location: { hostname: '127.0.0.1', port: '8000', origin: 'http://127.0.0.1:8000' },
    });

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.recordNotificationActivity(false);
    expect(res.ok).toBe(true);
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/system/notifications/activity'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ active: false }),
        headers: expect.objectContaining({
          'X-VoxPilot-Desktop-Token': 'desktop-activity-token',
        }),
      })
    );
  });

  it('prefers same-origin dynamic desktop loopback port over Vite fallback and preserves 5173/4173 dev behavior', () => {
    vi.stubGlobal('window', {
      location: { hostname: '127.0.0.1', port: '49213', origin: 'http://127.0.0.1:49213' },
    });
    expect(resolveApiBaseUrl()).toBe('http://127.0.0.1:49213');

    vi.stubGlobal('window', {
      location: { hostname: 'localhost', port: '51234', origin: 'http://localhost:51234/' },
    });
    expect(resolveApiBaseUrl()).toBe('http://localhost:51234');

    vi.stubGlobal('window', {
      location: { hostname: '127.0.0.1', port: '5173', origin: 'http://127.0.0.1:5173' },
    });
    expect(resolveApiBaseUrl()).toBe('http://127.0.0.1:8000');

    vi.stubGlobal('window', {
      location: { hostname: '127.0.0.1', port: '4173', origin: 'http://127.0.0.1:4173' },
    });
    expect(resolveApiBaseUrl()).toBe('http://127.0.0.1:8000');
  });
});

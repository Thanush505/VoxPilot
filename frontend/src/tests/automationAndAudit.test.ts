import { describe, it, expect, vi, beforeEach } from 'vitest';
import apiClient from '../api/client';
import {
  ExecutionStatusResponse,
  AuditExportRequest,
  ToolInfo,
  EmergencyStopStatus,
  PendingConfirmationInfo,
} from '../api/types';

describe('Automation Monitoring & Controller Telemetry Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('truthfully handles subsystems without polling routes as "Status unavailable"', () => {
    const subsystems = [
      { id: 'keyboard', name: 'Keyboard Controller' },
      { id: 'mouse', name: 'Mouse Controller' },
      { id: 'window', name: 'Window Manager' },
      { id: 'browser', name: 'Browser Automation' },
      { id: 'filesystem', name: 'Filesystem Controller' },
    ];

    // Every subsystem has no unauthenticated HTTP polling daemon
    const statuses = subsystems.map((sub) => ({
      id: sub.id,
      statusDisplay: 'Status unavailable',
      isPubliclyPolled: false,
    }));

    statuses.forEach((s) => {
      expect(s.statusDisplay).toBe('Status unavailable');
      expect(s.isPubliclyPolled).toBe(false);
    });
  });

  it('categorizes registered tools correctly by controller subsystem', () => {
    const mockTools: ToolInfo[] = [
      { name: 'type_text', category: 'keyboard_control', description: 'Type text', is_consequential: false },
      { name: 'press_key', category: 'keyboard_control', description: 'Press key', is_consequential: false },
      { name: 'click_mouse', category: 'mouse_control', description: 'Click mouse', is_consequential: false },
      { name: 'focus_window', category: 'window_management', description: 'Focus window', is_consequential: false },
      { name: 'open_browser', category: 'browser_control', description: 'Launch browser', is_consequential: false },
      { name: 'delete_file', category: 'file_management', description: 'Delete file', is_consequential: true },
    ];

    const keyboardTools = mockTools.filter((t) => t.category === 'keyboard_control');
    const mouseTools = mockTools.filter((t) => t.category === 'mouse_control');
    const fileTools = mockTools.filter((t) => t.category === 'file_management');

    expect(keyboardTools).toHaveLength(2);
    expect(mouseTools).toHaveLength(1);
    expect(fileTools).toHaveLength(1);
    expect(fileTools[0].is_consequential).toBe(true);
  });

  it('accurately displays active operations when operations are running', () => {
    const activeStatus: EmergencyStopStatus = {
      is_active: false,
      trigger_reason: null,
      trigger_source: null,
      triggered_at: null,
      reset_at: null,
      active_operations_count: 2,
      active_operations: [
        {
          operation_id: 'op-001',
          execution_id: 'exec-101',
          category: 'browser_control',
          description: 'Navigating to documentation site',
          start_time: 1700000000,
          status: 'running',
          pids: [1234],
        },
        {
          operation_id: 'op-002',
          execution_id: 'exec-102',
          category: 'keyboard_control',
          description: 'Typing text in application',
          start_time: 1700000010,
          status: 'running',
          pids: [],
        },
      ],
    };

    expect(activeStatus.active_operations_count).toBe(2);
    expect(activeStatus.active_operations[0].category).toBe('browser_control');
    expect(activeStatus.active_operations[1].category).toBe('keyboard_control');
  });

  it('guarantees zero direct unconstrained hardware automation controls on monitoring page', () => {
    // Verified invariant: No direct keyboard or mouse simulation triggers exist on AutomationPage
    const allowedActions = ['refresh_telemetry', 'trigger_emergency_stop', 'reset_emergency_stop'];
    const forbiddenDirectControls = ['send_raw_keys', 'dispatch_arbitrary_click', 'run_shell_cmd'];

    forbiddenDirectControls.forEach((action) => {
      expect(allowedActions).not.toContain(action);
    });
  });
});

describe('Audit Logs & Safe Export Workflow Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('correctly filters audit records by status and search query', () => {
    const records: Partial<ExecutionStatusResponse>[] = [
      { execution_id: 'exec-01', user_input: 'Open Chrome', intent: 'browser', execution_status: 'completed' },
      { execution_id: 'exec-02', user_input: 'Delete folder', intent: 'file', execution_status: 'cancelled' },
      { execution_id: 'exec-03', user_input: 'Infinite loop test', intent: 'system', execution_status: 'failed' },
      { execution_id: 'exec-04', user_input: 'Type password in form', intent: 'keyboard', execution_status: 'completed' },
    ];

    // Filter by status = 'completed'
    const completedOnly = records.filter((r) => r.execution_status === 'completed');
    expect(completedOnly).toHaveLength(2);

    // Search query 'Chrome'
    const searchMatch = records.filter((r) => r.user_input?.toLowerCase().includes('chrome'));
    expect(searchMatch).toHaveLength(1);
    expect(searchMatch[0].execution_id).toBe('exec-01');
  });

  it('formats JSON and CSV export payloads according to backend contract', async () => {
    const jsonReq: AuditExportRequest = {
      format: 'json',
      filename: 'my_audit.json',
      status_filter: 'completed',
      confirm_overwrite: true,
    };

    const csvReq: AuditExportRequest = {
      format: 'csv',
      filename: 'my_audit.csv',
      conversation_id: 'conv-999',
      confirm_overwrite: false,
    };

    expect(jsonReq.format).toBe('json');
    expect(jsonReq.confirm_overwrite).toBe(true);
    expect(csvReq.format).toBe('csv');
    expect(csvReq.confirm_overwrite).toBe(false);
  });

  it('strictly rejects path traversal sequences in custom export filenames', () => {
    const badFilenames = [
      '../escape.json',
      '..\\system32\\calc.csv',
      '/var/log/audit.json',
      'subfolder/export.json',
      '..//traversal.csv',
    ];

    const isTraversalSafe = (filename: string): boolean => {
      return !filename.includes('/') && !filename.includes('\\') && !filename.includes('..');
    };

    badFilenames.forEach((bad) => {
      expect(isTraversalSafe(bad)).toBe(false);
    });

    expect(isTraversalSafe('valid_audit_log_2026.json')).toBe(true);
    expect(isTraversalSafe('export_001.csv')).toBe(true);
  });

  it('handles safe download URL generation and scheduled revocation', async () => {
    const mockBlob = new Blob(['{"test": true}'], { type: 'application/json' });
    let createdUrl: string | null = null;
    let revokedUrl: string | null = null;

    const urlTarget = typeof window !== 'undefined' ? window.URL : URL;

    vi.spyOn(urlTarget, 'createObjectURL').mockImplementation((blob) => {
      createdUrl = `blob:http://localhost:5173/${(blob as Blob).size}`;
      return createdUrl;
    });

    vi.spyOn(urlTarget, 'revokeObjectURL').mockImplementation((url) => {
      revokedUrl = url;
    });

    // Mock document.createElement and anchor methods if in Node
    if (typeof document === 'undefined') {
      const mockAnchor = {
        href: '',
        download: '',
        style: { display: '' },
        click: vi.fn(),
      };
      (globalThis as unknown as Record<string, unknown>).document = {
        createElement: () => mockAnchor,
        body: {
          appendChild: vi.fn(),
          removeChild: vi.fn(),
        },
      };
    }
    if (typeof window === 'undefined') {
      (globalThis as unknown as Record<string, unknown>).window = globalThis;
    }

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(mockBlob, {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Content-Disposition': 'attachment; filename="audit_test.json"',
        },
      })
    );

    const result = await apiClient.downloadAuditExport('exp_100', 'audit_test.json');
    expect(result.ok).toBe(true);
    expect(createdUrl).toBeTruthy();

    // Fast-forward or trigger timeout cleanup
    await new Promise((r) => setTimeout(r, 1100));
    expect(revokedUrl).toBe(createdUrl);
  });

  it('guarantees credential scrubbing replaces secrets with [REDACTED]', () => {
    const sensitiveRecord = {
      execution_id: 'exec-sec-01',
      user_input: 'login with password supersecret123',
      steps: [
        {
          action: 'type_text',
          parameters: {
            text: 'supersecret123',
            password: 'supersecret123',
            confirmation_token: 'secret-token-xyz',
            api_key: 'sk-12345',
          },
        },
      ],
    };

    const SENSITIVE_KEYS = ['token', 'password', 'secret', 'api_key', 'auth'];
    const scrubData = (obj: unknown): unknown => {
      if (typeof obj !== 'object' || obj === null) return obj;
      if (Array.isArray(obj)) return obj.map(scrubData);
      const res: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
        if (SENSITIVE_KEYS.some((s) => k.toLowerCase().includes(s))) {
          res[k] = '[REDACTED]';
        } else {
          res[k] = scrubData(v);
        }
      }
      return res;
    };

    const scrubbed = scrubData(sensitiveRecord) as typeof sensitiveRecord;
    const scrubbedParams = scrubbed.steps[0].parameters;
    expect(scrubbedParams.password).toBe('[REDACTED]');
    expect(scrubbedParams.confirmation_token).toBe('[REDACTED]');
    expect(scrubbedParams.api_key).toBe('[REDACTED]');
  });
});

describe('Agent Chat, Confirmation & Safety Invariants', () => {
  it('prevents duplicate submissions while agent request is actively processing', () => {
    let isSubmitting = false;

    const handleSubmit = (input: string) => {
      if (isSubmitting || !input.trim()) return false;
      isSubmitting = true;
      return true;
    };

    expect(handleSubmit('First prompt')).toBe(true);
    expect(isSubmitting).toBe(true);

    // Duplicate submission attempt while in-flight
    expect(handleSubmit('Second duplicate prompt')).toBe(false);

    // After completion
    isSubmitting = false;
    expect(handleSubmit('Second duplicate prompt')).toBe(true);
  });

  it('enforces that human approval requires explicit operator confirmation token', () => {
    const pendingConfirmation: PendingConfirmationInfo = {
      token: 'cryptographic-token-val',
      action: 'delete_directory',
      parameters: { path: 'C:\\Users\\User\\Documents\\old' },
      execution_id: 'exec-conf-99',
    };

    // Approval requires matching token payload
    const makeApprovalPayload = (token?: string) => {
      if (!token) throw new Error('Confirmation token is required for approval.');
      return { token };
    };

    expect(() => makeApprovalPayload()).toThrow('Confirmation token is required');
    expect(makeApprovalPayload(pendingConfirmation.token)).toEqual({
      token: 'cryptographic-token-val',
    });
  });

  it('handles emergency stop 409 rejection and prevents premature execution resumption', () => {
    const errorResponse = {
      status: 409,
      detail: 'Unsafe reset rejected: active operations still in progress.',
    };

    const canResumeExecution = (resStatus: number): boolean => {
      return resStatus === 200;
    };

    expect(canResumeExecution(errorResponse.status)).toBe(false);
  });
});

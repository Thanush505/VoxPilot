import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from '../api/client';

describe('Phase 5C Desktop Notification API Client', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches, updates, tests, and resets notification preferences', async () => {
    const sampleStatus = {
      supported: true,
      platform: 'win32',
      mode: 'development',
      mechanism: 'windows_winrt_toast',
      app_user_model_id: 'WindowsPowerShell',
      active_profile_id: 'default',
      ui_recently_active: false,
      preferences: {
        enabled: true,
        suppress_when_active: true,
        categories: {
          task_completed: true,
          task_failed: true,
        },
      },
      categories_meta: [
        { id: 'task_completed', label: 'Task Completed', description: 'Task finished.' },
      ],
    };

    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('/api/system/notifications/test')) {
        return new Response(
          JSON.stringify({
            delivered: true,
            suppressed: false,
            category: 'task_completed',
            title: 'VoxPilot Notifications Active',
            message: 'Desktop notifications are working.',
            status: sampleStatus,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify(sampleStatus), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    vi.stubGlobal('fetch', fetchMock);

    const getRes = await apiClient.getNotificationStatus();
    expect(getRes.ok).toBe(true);
    expect(getRes.data?.supported).toBe(true);
    expect(getRes.data?.preferences.enabled).toBe(true);

    const updRes = await apiClient.updateNotificationPreferences({ enabled: false });
    expect(updRes.ok).toBe(true);

    const testRes = await apiClient.sendTestNotification();
    expect(testRes.ok).toBe(true);
    expect(testRes.data?.delivered).toBe(true);

    const resetRes = await apiClient.resetNotificationPreferences();
    expect(resetRes.ok).toBe(true);

    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import apiClient from '../api/client';
import {
  AppVersionResponse,
  UpdateStatusResponse,
  UpdateSettings,
} from '../api/types';
import {
  formatBytes,
  formatPublishedDate,
} from '../utils/updateUtils';

describe('In-App Software Update System Frontend Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const makeMockUpdateStatus = (
    overrides?: Partial<UpdateStatusResponse>
  ): UpdateStatusResponse => ({
    state: 'up_to_date',
    installed_version: '0.5.0',
    latest_version: '0.5.0',
    update_available: false,
    notification_dismissed: false,
    channel: 'stable',
    auto_check_enabled: true,
    auto_install_enabled: false,
    release_repo: 'Thanush505/VoxPilot',
    last_checked_at: '2026-11-11T16:00:00Z',
    release: null,
    download: {
      downloaded_bytes: 0,
      total_bytes: 0,
      progress_pct: 0,
      installer_path: null,
      computed_sha256: null,
      sha256_verified: false,
      signature: null,
    },
    last_install_result: null,
    status_message: 'VoxPilot v0.5.0 is up to date.',
    error_code: null,
    error_message: null,
    ...overrides,
  });

  it('1. retrieves authoritative installed version from GET /api/version', async () => {
    const mockVersion: AppVersionResponse = {
      app_name: 'VoxPilot',
      installed_version: '0.5.0',
      is_prerelease: false,
      channel: 'stable',
      release_repo: 'Thanush505/VoxPilot',
      is_frozen: false,
      last_checked_at: '2026-11-11T16:00:00Z',
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockVersion), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.getVersion();
    expect(res.ok).toBe(true);
    expect(res.data?.installed_version).toBe('0.5.0');
    expect(res.data?.release_repo).toBe('Thanush505/VoxPilot');
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('2. handles update notification state when a newer version is available', async () => {
    const availableStatus = makeMockUpdateStatus({
      state: 'update_available',
      installed_version: '0.5.0',
      latest_version: '0.6.0',
      update_available: true,
      release: {
        version: '0.6.0',
        tag_name: 'v0.6.0',
        title: 'VoxPilot v0.6.0 Official Release',
        notes: '### New in v0.6.0\n- In-app software update system\n- Security enhancements',
        published_at: '2026-11-11T12:00:00Z',
        release_url: 'https://github.com/Thanush505/VoxPilot/releases/tag/v0.6.0',
        installer_name: 'VoxPilot-Setup.exe',
        installer_url:
          'https://github.com/Thanush505/VoxPilot/releases/download/v0.6.0/VoxPilot-Setup.exe',
        installer_size_bytes: 233334015,
        expected_sha256:
          '0036bc257fabca414e9f0aa0b5a91554c04c292272b09a9d04fe50154285dae0',
      },
      status_message: 'Update available: VoxPilot v0.6.0 (currently installed: v0.5.0).',
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(availableStatus), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.getUpdateStatus();
    expect(res.ok).toBe(true);
    expect(res.data?.update_available).toBe(true);
    expect(res.data?.installed_version).toBe('0.5.0');
    expect(res.data?.latest_version).toBe('0.6.0');
    expect(res.data?.release?.title).toBe('VoxPilot v0.6.0 Official Release');
    expect(formatBytes(res.data?.release?.installer_size_bytes)).toBe('222.5 MB');
    expect(formatPublishedDate(res.data?.release?.published_at)).toContain('2026');
  });

  it('3. handles no-update state when installed version is already current', async () => {
    const upToDate = makeMockUpdateStatus({
      state: 'up_to_date',
      installed_version: '0.5.0',
      latest_version: '0.5.0',
      update_available: false,
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(upToDate), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.getUpdateStatus();
    expect(res.ok).toBe(true);
    expect(res.data?.state).toBe('up_to_date');
    expect(res.data?.update_available).toBe(false);
  });

  it('4. triggers manual update check via POST /api/updates/check with force=true', async () => {
    const checkedStatus = makeMockUpdateStatus({
      state: 'up_to_date',
      status_message: 'VoxPilot v0.5.0 is up to date.',
    });

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(checkedStatus), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.checkForUpdates(true);
    expect(res.ok).toBe(true);
    expect(res.data?.state).toBe('up_to_date');
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/updates/check'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ force: true }),
      })
    );
  });

  it('5. tracks download progress and supports download cancellation', async () => {
    const downloadingStatus = makeMockUpdateStatus({
      state: 'downloading',
      update_available: true,
      latest_version: '0.6.0',
      download: {
        downloaded_bytes: 104857600,
        total_bytes: 209715200,
        progress_pct: 50.0,
        installer_path: null,
        computed_sha256: null,
        sha256_verified: false,
        signature: null,
      },
      status_message: 'Downloading VoxPilot v0.6.0 installer...',
    });

    const cancelledStatus = makeMockUpdateStatus({
      state: 'update_available',
      update_available: true,
      latest_version: '0.6.0',
      status_message: 'Update download cancelled.',
    });

    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify(downloadingStatus), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(cancelledStatus), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      );

    const dlRes = await apiClient.startUpdateDownload(true);
    expect(dlRes.ok).toBe(true);
    expect(dlRes.data?.state).toBe('downloading');
    expect(dlRes.data?.download.progress_pct).toBe(50.0);
    expect(formatBytes(dlRes.data?.download.downloaded_bytes)).toBe('100.0 MB');

    const cancelRes = await apiClient.cancelUpdateDownload();
    expect(cancelRes.ok).toBe(true);
    expect(cancelRes.data?.state).toBe('update_available');
    expect(cancelRes.data?.status_message).toContain('cancelled');
  });

  it('6. surfaces download and checksum verification errors clearly', async () => {
    const failedStatus = makeMockUpdateStatus({
      state: 'failed',
      update_available: true,
      latest_version: '0.6.0',
      error_code: 'checksum_mismatch',
      error_message:
        'SHA-256 checksum verification failed! The corrupted or unverified file has been deleted.',
      status_message: 'SHA-256 checksum verification failed!',
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(failedStatus), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.getUpdateStatus();
    expect(res.ok).toBe(true);
    expect(res.data?.state).toBe('failed');
    expect(res.data?.error_code).toBe('checksum_mismatch');
    expect(res.data?.error_message).toContain('SHA-256 checksum verification failed');
  });

  it('7. reports SHA-256 verification and Authenticode signature status when ready to install', async () => {
    const verifiedHash =
      '0036bc257fabca414e9f0aa0b5a91554c04c292272b09a9d04fe50154285dae0';
    const readyStatus = makeMockUpdateStatus({
      state: 'ready_to_install',
      update_available: true,
      latest_version: '0.6.0',
      download: {
        downloaded_bytes: 233334015,
        total_bytes: 233334015,
        progress_pct: 100.0,
        installer_path:
          'C:\\Users\\Thanu\\AppData\\Local\\VoxPilot\\updates\\VoxPilot-Setup-0.6.0.exe',
        computed_sha256: verifiedHash,
        sha256_verified: true,
        signature: {
          signature_checked: true,
          signature_status: 'NotSigned',
          signature_valid: false,
          signer_subject: null,
          integrity_verification:
            'SHA-256 checksum verified against official release metadata',
          publisher_verification_note:
            'Package integrity verified via official SHA-256 checksum. Installer binary is not Authenticode code-signed (unsigned build).',
        },
      },
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(readyStatus), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.getUpdateStatus();
    expect(res.ok).toBe(true);
    expect(res.data?.state).toBe('ready_to_install');
    expect(res.data?.download.sha256_verified).toBe(true);
    expect(res.data?.download.computed_sha256).toBe(verifiedHash);
    expect(res.data?.download.signature?.integrity_verification).toContain('SHA-256');
  });

  it('8. requires explicit confirmation to trigger external update installation', async () => {
    const installingStatus = makeMockUpdateStatus({
      state: 'installing',
      update_available: true,
      latest_version: '0.6.0',
      status_message:
        'External updater launched for VoxPilot v0.6.0. Closing VoxPilot so the installer can upgrade the application...',
    });

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(installingStatus), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.installUpdate(true, true, true);
    expect(res.ok).toBe(true);
    expect(res.data?.state).toBe('installing');
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/updates/install'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          confirm: true,
          relaunch: true,
          shutdown_app: true,
        }),
      })
    );
  });

  it('9. persists Remind Me Later dismissal and update preferences (auto-check & channel)', async () => {
    const dismissedStatus = makeMockUpdateStatus({
      state: 'update_available',
      update_available: true,
      latest_version: '0.6.0',
      notification_dismissed: true,
    });

    const updatedSettings: { status: string; settings: UpdateSettings } = {
      status: 'updated',
      settings: {
        auto_check_enabled: false,
        channel: 'beta',
        release_repo: 'Thanush505/VoxPilot',
        last_checked_at: '2026-11-11T16:00:00Z',
        last_available_version: '0.6.0',
        dismissed_version: '0.6.0',
        auto_install_enabled: false,
      },
    };

    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify(dismissedStatus), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(updatedSettings), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      );

    const dismissRes = await apiClient.dismissUpdateNotification('0.6.0');
    expect(dismissRes.ok).toBe(true);
    expect(dismissRes.data?.notification_dismissed).toBe(true);

    const settingsRes = await apiClient.updateUpdateSettings({
      auto_check_enabled: false,
      channel: 'beta',
    });
    expect(settingsRes.ok).toBe(true);
    expect(settingsRes.data?.settings.auto_check_enabled).toBe(false);
    expect(settingsRes.data?.settings.channel).toBe('beta');
    expect(settingsRes.data?.settings.auto_install_enabled).toBe(false);
  });
});

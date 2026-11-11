import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import apiClient from '../api/client';
import {
  ApplicationsListResponse,
  DirectLaunchResponse,
} from '../api/types';

describe('Universal Windows Application Discovery & Launcher Frontend Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const makeMockApplicationsResponse = (): ApplicationsListResponse => ({
    applications: [
      {
        app_id: 'whatsapp',
        name: 'whatsapp',
        display_name: 'WhatsApp',
        executable_path: 'shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App',
        source: 'microsoft_store',
        launch_method: 'store_app',
        publisher: 'Meta Platforms, Inc.',
        install_location: null,
        package_id: '5319275A.WhatsAppDesktop_cv1g1gvanyjgm',
        arguments: [],
        aliases: ['whatsapp', 'whats app'],
        requires_confirmation: false,
        discovered_at: '2026-11-11T17:00:00Z',
      },
      {
        app_id: 'settings-bluetooth',
        name: 'bluetooth & devices settings',
        display_name: 'Bluetooth & Devices Settings',
        executable_path: 'ms-settings:bluetooth',
        source: 'windows_settings',
        launch_method: 'settings_uri',
        publisher: 'Microsoft Corporation',
        install_location: null,
        package_id: null,
        arguments: [],
        aliases: ['bluetooth settings', 'bluetooth'],
        requires_confirmation: false,
        discovered_at: '2026-11-11T17:00:00Z',
      },
      {
        app_id: 'utility-device-manager',
        name: 'device manager',
        display_name: 'Device Manager',
        executable_path: 'C:\\Windows\\System32\\control.exe',
        source: 'system_utility',
        launch_method: 'system_utility',
        publisher: 'Microsoft Corporation',
        install_location: null,
        package_id: null,
        arguments: ['hdwwiz.cpl'],
        aliases: ['device manager', 'devmgmt'],
        requires_confirmation: true,
        discovered_at: '2026-11-11T17:00:00Z',
      },
    ],
    returned_count: 3,
    total_count: 3,
    by_source: {
      microsoft_store: 1,
      windows_settings: 1,
      system_utility: 1,
    },
    last_refreshed_at: '2026-11-11T17:00:00Z',
    errors: [],
  });

  it('1. lists discovered applications with optional search and source filter via GET /agent/applications', async () => {
    const mockData = makeMockApplicationsResponse();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.getApplications('whatsapp', 'microsoft_store', 100);
    expect(res.ok).toBe(true);
    expect(res.data?.total_count).toBe(3);
    expect(res.data?.applications[0].display_name).toBe('WhatsApp');
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/agent/applications?query=whatsapp&source=microsoft_store&limit=100'),
      expect.any(Object)
    );
  });

  it('2. refreshes the application discovery index via POST /agent/applications/refresh', async () => {
    const mockData: ApplicationsListResponse = {
      ...makeMockApplicationsResponse(),
      status: 'refreshed',
      message: 'Application index refreshed (3 applications indexed).',
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.refreshApplications(true);
    expect(res.ok).toBe(true);
    expect(res.data?.status).toBe('refreshed');
    expect(res.data?.message).toContain('refreshed');
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/agent/applications/refresh'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ include_store_apps: true }),
      })
    );
  });

  it('3. launches a discovered application via POST /agent/applications/launch', async () => {
    const launchResult: DirectLaunchResponse = {
      success: true,
      app_id: 'whatsapp',
      display_name: 'WhatsApp',
      pid: 9876,
      status: 'launched',
      launch_method: 'store_app',
      source: 'microsoft_store',
      message: 'Successfully launched WhatsApp (PID: 9876).',
      timestamp: '2026-11-11T17:05:00Z',
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(launchResult), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.launchApplication('WhatsApp');
    expect(res.ok).toBe(true);
    expect(res.data?.success).toBe(true);
    expect(res.data?.launch_method).toBe('store_app');
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/agent/applications/launch'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ query: 'WhatsApp', confirmation_token: null }),
      })
    );
  });

  it('4. handles confirmation_required response for sensitive system utilities', async () => {
    const confirmResult: DirectLaunchResponse = {
      success: false,
      status: 'confirmation_required',
      requires_confirmation: true,
      confirmation_token: 'tok_devmgmt_01',
      display_name: 'Device Manager',
      message: "Launching 'Device Manager' requires explicit confirmation.",
    };

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(confirmResult), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.launchApplication('Device Manager');
    expect(res.ok).toBe(true);
    expect(res.data?.status).toBe('confirmation_required');
    expect(res.data?.requires_confirmation).toBe(true);
    expect(res.data?.confirmation_token).toBe('tok_devmgmt_01');
  });
});

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  Bot,
  Cpu,
  HardDrive,
  Info,
  Layers,
  Monitor,
  Play,
  RefreshCw,
  Square,
  Thermometer,
} from 'lucide-react';
import apiClient from '../../api/client';
import { SystemHardwareResponse, SystemModelsResponse } from '../../api/types';

interface SystemOverviewProps {
  isBackendConnected?: boolean;
}

const POLL_INTERVAL_MS = 15000;

function formatNumber(value: number | null | undefined, digits: number = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return value.toFixed(digits);
}

function getTelemetryLevelClass(percent: number | null | undefined): string {
  if (percent === null || percent === undefined || Number.isNaN(percent)) {
    return '';
  }
  if (percent >= 90) return 'level-high';
  if (percent >= 75) return 'level-warm';
  return '';
}

export const SystemOverview: React.FC<SystemOverviewProps> = ({
  isBackendConnected = true,
}) => {
  const [hardware, setHardware] = useState<SystemHardwareResponse | null>(null);
  const [modelsInfo, setModelsInfo] = useState<SystemModelsResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [busyModel, setBusyModel] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);

  const isMountedRef = useRef<boolean>(true);
  const inFlightRef = useRef<boolean>(false);

  const fetchTelemetry = useCallback(async (isManual: boolean = false) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (isManual && isMountedRef.current) {
      setIsRefreshing(true);
    }

    try {
      const [hwRes, modelsRes] = await Promise.all([
        apiClient.getSystemHardware(),
        apiClient.getSystemModels(),
      ]);

      if (!isMountedRef.current) return;

      if (hwRes.ok && hwRes.data) {
        setHardware(hwRes.data);
        setError(null);
      } else {
        setError(hwRes.error || 'Unable to retrieve host hardware metrics.');
      }

      if (modelsRes.ok && modelsRes.data) {
        setModelsInfo(modelsRes.data);
      }
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      setError(err instanceof Error ? err.message : 'Failed to load hardware telemetry.');
    } finally {
      inFlightRef.current = false;
      if (isMountedRef.current) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    fetchTelemetry(false);

    const timer = setInterval(() => {
      if (isBackendConnected) {
        fetchTelemetry(false);
      }
    }, POLL_INTERVAL_MS);

    return () => {
      isMountedRef.current = false;
      clearInterval(timer);
    };
  }, [fetchTelemetry, isBackendConnected]);

  const handleLoadModel = async (modelName: string) => {
    setBusyModel(modelName);
    setActionNotice(null);
    const res = await apiClient.loadSystemModel(modelName);
    if (!isMountedRef.current) return;
    setBusyModel(null);

    if (res.ok && res.data) {
      setActionNotice({ type: 'success', message: res.data.message });
      await fetchTelemetry(true);
    } else {
      setActionNotice({
        type: 'error',
        message: res.error || `Failed to load model '${modelName}'.`,
      });
    }
  };

  const handleUnloadModel = async (modelName: string) => {
    setBusyModel(modelName);
    setActionNotice(null);
    const res = await apiClient.unloadSystemModel(modelName);
    if (!isMountedRef.current) return;
    setBusyModel(null);

    if (res.ok && res.data) {
      setActionNotice({ type: 'success', message: res.data.message });
      await fetchTelemetry(true);
    } else {
      setActionNotice({
        type: 'error',
        message: res.error || `Failed to unload model '${modelName}'.`,
      });
    }
  };

  const cpu = hardware?.cpu;
  const ram = hardware?.ram;
  const gpu = hardware?.gpu;
  const ollama = hardware?.ollama;
  const installedModels = modelsInfo?.installed_models ?? ollama?.installed_models ?? [];
  const runningModels = modelsInfo?.running_models ?? ollama?.running_models ?? [];
  const ollamaReachable = modelsInfo?.reachable ?? ollama?.reachable ?? false;
  const ollamaError = modelsInfo?.error ?? ollama?.error ?? null;
  const activeModel = modelsInfo?.active_model ?? 'qwen3:4b';
  const defaultModel = modelsInfo?.default_model ?? 'qwen3:4b';
  const runningModelNames = new Set(runningModels.map((m) => m.name.toLowerCase()));

  return (
    <div className="dashboard-card">
      <div className="dashboard-card-header">
        <div className="dashboard-card-title-group">
          <Cpu size={18} />
          <div>
            <h2 className="dashboard-card-title">Host Hardware & Local AI Runtime</h2>
            <p className="dashboard-card-description">
              Live read-only CPU, RAM, NVIDIA GPU/VRAM telemetry and local Ollama model lifecycle.
            </p>
          </div>
        </div>
        <button
          type="button"
          className="btn-download"
          onClick={() => fetchTelemetry(true)}
          disabled={isRefreshing || busyModel !== null}
          title="Refresh hardware and Ollama telemetry"
        >
          <RefreshCw size={13} className={isRefreshing ? 'spin-icon' : ''} />
          <span>{isRefreshing ? 'Refreshing...' : 'Refresh'}</span>
        </button>
      </div>

      {/* Loading State (initial) */}
      {isLoading && !hardware && (
        <div className="system-unavailable-notice">
          <div className="notice-icon">
            <Activity size={20} />
          </div>
          <div className="notice-content">
            <div className="notice-title">Collecting Host Telemetry...</div>
            <p className="notice-description">
              Reading local CPU, memory, GPU, and Ollama model status from <code>/api/system/hardware</code>.
            </p>
          </div>
        </div>
      )}

      {/* Error / Offline State */}
      {error && !hardware && !isLoading && (
        <div className="system-unavailable-notice system-error-notice">
          <div className="notice-icon">
            <Info size={20} />
          </div>
          <div className="notice-content">
            <div className="notice-title">Hardware Telemetry Unavailable</div>
            <p className="notice-description">{error}</p>
          </div>
        </div>
      )}

      {error && hardware && (
        <div className="system-unavailable-notice system-error-notice">
          <div className="notice-icon">
            <Info size={18} />
          </div>
          <div className="notice-content">
            <div className="notice-title">Last Refresh Encountered an Error</div>
            <p className="notice-description">{error}</p>
          </div>
        </div>
      )}

      {/* Hardware Metrics Grid */}
      <div className="metrics-grid vp-hardware-metrics-grid">
        {/* Host OS */}
        <div className={`metric-box ${!hardware ? 'disabled' : ''}`}>
          <div className="metric-box-header">
            <span>Host Platform</span>
            <Monitor size={14} />
          </div>
          <div className="metric-box-value">
            {hardware ? `${hardware.os_name} ${hardware.os_release}` : '—'}
          </div>
          <div className="metric-box-footer">
            {hardware ? `Arch: ${hardware.machine}` : 'Awaiting host telemetry'}
          </div>
        </div>

        {/* CPU */}
        <div className={`metric-box ${!cpu ? 'disabled' : ''}`}>
          <div className="metric-box-header">
            <span>CPU Usage</span>
            <Cpu size={14} />
          </div>
          <div className="metric-box-value">
            {cpu ? `${formatNumber(cpu.utilization_percent, 1)} %` : '— %'}
          </div>
          {cpu && (
            <div className="telemetry-bar-track">
              <div
                className={`telemetry-bar-fill ${getTelemetryLevelClass(cpu.utilization_percent)}`}
                style={{ width: `${Math.min(100, Math.max(0, cpu.utilization_percent))}%` }}
              />
            </div>
          )}
          <div className="metric-box-footer">
            {cpu
              ? `${cpu.logical_cores} logical / ${cpu.physical_cores ?? '—'} physical` +
              (cpu.frequency_mhz ? ` • ${Math.round(cpu.frequency_mhz)} MHz` : '') +
              (cpu.temperature_available && cpu.temperature_celsius !== null
                ? ` • ${cpu.temperature_celsius}°C`
                : '')
              : 'CPU telemetry unavailable'}
          </div>
        </div>

        {/* RAM */}
        <div className={`metric-box ${!ram ? 'disabled' : ''}`}>
          <div className="metric-box-header">
            <span>System RAM</span>
            <HardDrive size={14} />
          </div>
          <div className="metric-box-value">
            {ram ? `${formatNumber(ram.utilization_percent, 1)} %` : '— %'}
          </div>
          {ram && (
            <div className="telemetry-bar-track">
              <div
                className={`telemetry-bar-fill ${getTelemetryLevelClass(ram.utilization_percent)}`}
                style={{ width: `${Math.min(100, Math.max(0, ram.utilization_percent))}%` }}
              />
            </div>
          )}
          <div className="metric-box-footer">
            {ram
              ? `${ram.used_gb.toFixed(1)} / ${ram.total_gb.toFixed(1)} GB (${ram.available_gb.toFixed(1)} GB free)`
              : 'Memory telemetry unavailable'}
          </div>
        </div>

        {/* GPU Usage & Temperature */}
        <div className={`metric-box ${!gpu?.available ? 'disabled' : ''}`}>
          <div className="metric-box-header">
            <span>GPU Compute & Temp</span>
            <Thermometer size={14} />
          </div>
          <div className="metric-box-value">
            {gpu?.available
              ? `${gpu.utilization_percent !== null ? `${formatNumber(gpu.utilization_percent, 0)}%` : '—'}` +
              ` • ${gpu.temperature_celsius !== null ? `${formatNumber(gpu.temperature_celsius, 1)}°C` : '—°C'}`
              : 'Unavailable'}
          </div>
          {gpu?.available && gpu.utilization_percent !== null && (
            <div className="telemetry-bar-track">
              <div
                className={`telemetry-bar-fill ${getTelemetryLevelClass(gpu.utilization_percent)}`}
                style={{ width: `${Math.min(100, Math.max(0, gpu.utilization_percent))}%` }}
              />
            </div>
          )}
          <div className="metric-box-footer">
            {gpu?.available
              ? gpu.name || 'NVIDIA GPU'
              : gpu?.status_reason || 'NVIDIA tooling unavailable'}
          </div>
        </div>

        {/* GPU VRAM */}
        <div className={`metric-box ${!gpu?.available || gpu.vram_total_mb === null ? 'disabled' : ''}`}>
          <div className="metric-box-header">
            <span>GPU VRAM</span>
            <Layers size={14} />
          </div>
          <div className="metric-box-value">
            {gpu?.available && gpu.vram_utilization_percent !== null
              ? `${formatNumber(gpu.vram_utilization_percent, 1)} %`
              : 'Unavailable'}
          </div>
          {gpu?.available && gpu.vram_utilization_percent !== null && (
            <div className="telemetry-bar-track">
              <div
                className="telemetry-bar-fill telemetry-bar-vram"
                style={{ width: `${Math.min(100, Math.max(0, gpu.vram_utilization_percent))}%` }}
              />
            </div>
          )}
          <div className="metric-box-footer">
            {gpu?.available && gpu.vram_total_mb !== null && gpu.vram_used_mb !== null
              ? `${Math.round(gpu.vram_used_mb)} / ${Math.round(gpu.vram_total_mb)} MB (${Math.round(gpu.vram_free_mb ?? 0)} MB free)`
              : gpu?.status_reason || 'VRAM telemetry unavailable'}
          </div>
        </div>
      </div>

      {/* Ollama Runtime & Model Lifecycle Section */}
      <div
        className={`ollama-runtime-panel ${
          ollamaReachable ? 'ollama-runtime-panel--healthy' : 'ollama-runtime-panel--unreachable'
        }`}
      >
        <div className="ollama-runtime-header">
          <div className="ollama-runtime-title-group">
            <Bot size={16} />
            <span className="ollama-runtime-title">Local Ollama Runtime</span>
            <span className={`status-pill ${ollamaReachable ? 'success' : 'danger'}`}>
              {ollamaReachable ? 'Reachable' : 'Unreachable'}
            </span>
          </div>
          <div className="ollama-runtime-meta">
            <span>
              Primary Model: <code>{defaultModel}</code>
            </span>
            <span>
              Active: <code>{activeModel}</code>
            </span>
          </div>
        </div>

        {actionNotice && (
          <div
            className={`ollama-action-notice ${actionNotice.type === 'error' ? 'error' : 'success'
              }`}
          >
            {actionNotice.message}
          </div>
        )}

        {!ollamaReachable ? (
          <p className="notice-description">
            {ollamaError || 'Local Ollama service is currently unreachable. Start Ollama to manage local models.'}
          </p>
        ) : (
          <div className="ollama-models-columns">
            {/* Installed Models */}
            <div className="ollama-models-subsection">
              <div className="ollama-subsection-heading">
                Installed Models ({installedModels.length})
              </div>
              {installedModels.length === 0 ? (
                <div className="ollama-empty-note">No installed Ollama models reported.</div>
              ) : (
                <div className="ollama-models-list">
                  {installedModels.map((model) => {
                    const isRunning = runningModelNames.has(model.name.toLowerCase());
                    const isBusy = busyModel === model.name;
                    return (
                      <div key={model.name} className="ollama-model-row">
                        <div className="ollama-model-info">
                          <div className="ollama-model-name-line">
                            <span className="ollama-model-name">{model.name}</span>
                            {model.name === defaultModel && (
                              <span className="status-pill inactive">Primary</span>
                            )}
                            {isRunning && <span className="status-pill success">Loaded</span>}
                          </div>
                          <div className="ollama-model-meta">
                            <span>{model.size_gb.toFixed(2)} GB</span>
                            {model.parameter_size && <span>{model.parameter_size}</span>}
                            {model.quantization_level && <span>{model.quantization_level}</span>}
                          </div>
                        </div>
                        <div className="ollama-model-actions">
                          {isRunning ? (
                            <button
                              type="button"
                              className="btn-model-action unload"
                              onClick={() => handleUnloadModel(model.name)}
                              disabled={busyModel !== null}
                            >
                              <Square size={12} />
                              <span>{isBusy ? 'Unloading...' : 'Unload'}</span>
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="btn-model-action load"
                              onClick={() => handleLoadModel(model.name)}
                              disabled={busyModel !== null}
                            >
                              <Play size={12} />
                              <span>{isBusy ? 'Loading...' : 'Load'}</span>
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Currently Loaded Models */}
            <div className="ollama-models-subsection">
              <div className="ollama-subsection-heading">
                Currently Loaded in Memory / VRAM ({runningModels.length})
              </div>
              {runningModels.length === 0 ? (
                <div className="ollama-empty-note">
                  No models currently loaded in VRAM (idle on demand).
                </div>
              ) : (
                <div className="ollama-models-list">
                  {runningModels.map((model) => {
                    const isBusy = busyModel === model.name;
                    return (
                      <div key={model.name} className="ollama-model-row">
                        <div className="ollama-model-info">
                          <div className="ollama-model-name-line">
                            <span className="ollama-model-name">{model.name}</span>
                            <span className="status-pill success">Running</span>
                          </div>
                          <div className="ollama-model-meta">
                            <span>VRAM: {Math.round(model.size_vram_mb)} MB</span>
                            {model.vram_percent !== null && (
                              <span>Offload: {model.vram_percent}%</span>
                            )}
                            {model.context_length && <span>Ctx: {model.context_length}</span>}
                          </div>
                        </div>
                        <div className="ollama-model-actions">
                          <button
                            type="button"
                            className="btn-model-action unload"
                            onClick={() => handleUnloadModel(model.name)}
                            disabled={busyModel !== null}
                          >
                            <Square size={12} />
                            <span>{isBusy ? 'Unloading...' : 'Unload'}</span>
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default SystemOverview;

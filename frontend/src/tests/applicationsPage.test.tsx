import React, { useState, useEffect, useCallback } from 'react';
import {
  AppWindow,
  Search,
  RefreshCw,
  Play,
  ShieldAlert,
  CheckCircle2,
  AlertTriangle,
  Layers,
  Filter,
} from 'lucide-react';
import apiClient from '../api/client';
import {
  DiscoveredApplicationItem,
  DirectLaunchResponse,
} from '../api/types';
import LoadingState from '../components/common/LoadingState';
import ErrorState from '../components/common/ErrorState';

interface ApplicationsPageProps {
  isBackendConnected: boolean;
}

const SOURCE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'all', label: 'All Sources' },
  { value: 'start_menu', label: 'Start Menu' },
  { value: 'registry', label: 'Windows Registry' },
  { value: 'microsoft_store', label: 'Microsoft Store / Appx' },
  { value: 'known_directory', label: 'Program Files / LocalAppData' },
  { value: 'desktop_shortcut', label: 'Desktop Shortcuts' },
  { value: 'windows_settings', label: 'Windows Settings' },
  { value: 'system_utility', label: 'System Utilities' },
  { value: 'special_folder', label: 'Special Folders' },
  { value: 'builtin', label: 'Built-in Apps' },
];

const formatSourceLabel = (source: string): string => {
  const found = SOURCE_OPTIONS.find((opt) => opt.value === source);
  return found ? found.label : source.replace(/_/g, ' ');
};

export const ApplicationsPage: React.FC<ApplicationsPageProps> = ({
  isBackendConnected,
}) => {
  const [applications, setApplications] = useState<DiscoveredApplicationItem[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [bySource, setBySource] = useState<Record<string, number>>({});
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string | null>(null);

  const [searchQuery, setSearchQuery] = useState<string>('');
  const [sourceFilter, setSourceFilter] = useState<string>('all');

  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [launchingId, setLaunchingId] = useState<string | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [statusBanner, setStatusBanner] = useState<{
    type: 'success' | 'warning' | 'error';
    message: string;
  } | null>(null);

  const [pendingConfirmLaunch, setPendingConfirmLaunch] = useState<{
    app: DiscoveredApplicationItem;
    message: string;
  } | null>(null);

  const fetchApplications = useCallback(
    async (queryOverride?: string, sourceOverride?: string) => {
      if (!isBackendConnected) {
        setApplications([]);
        return;
      }

      setIsLoading(true);
      setError(null);
      try {
        const q = queryOverride !== undefined ? queryOverride : searchQuery;
        const src = sourceOverride !== undefined ? sourceOverride : sourceFilter;
        const res = await apiClient.getApplications(q, src, 250);
        if (res.ok && res.data) {
          setApplications(res.data.applications || []);
          setTotalCount(res.data.total_count ?? 0);
          setBySource(res.data.by_source || {});
          setLastRefreshedAt(res.data.last_refreshed_at || null);
        } else {
          setError(res.error || 'Failed to load discovered Windows applications.');
        }
      } catch (err: unknown) {
        setError(
          err instanceof Error
            ? err.message
            : 'Unexpected error loading discovered applications.'
        );
      } finally {
        setIsLoading(false);
      }
    },
    [isBackendConnected, searchQuery, sourceFilter]
  );

  useEffect(() => {
    fetchApplications();
  }, [fetchApplications]);

  const handleRefreshIndex = async () => {
    if (!isBackendConnected || isRefreshing) return;
    setIsRefreshing(true);
    setStatusBanner(null);
    setError(null);
    try {
      const res = await apiClient.refreshApplications(true);
      if (res.ok && res.data) {
        setApplications(res.data.applications || []);
        setTotalCount(res.data.total_count ?? 0);
        setBySource(res.data.by_source || {});
        setLastRefreshedAt(res.data.last_refreshed_at || null);
        setStatusBanner({
          type: 'success',
          message:
            res.data.message ||
            `Refreshed application index (${res.data.total_count} items discovered).`,
        });
      } else {
        setStatusBanner({
          type: 'error',
          message: res.error || 'Failed to refresh the application discovery index.',
        });
      }
    } catch (err: unknown) {
      setStatusBanner({
        type: 'error',
        message:
          err instanceof Error
            ? err.message
            : 'Error refreshing application index.',
      });
    } finally {
      setIsRefreshing(false);
    }
  };

  const executeLaunch = async (app: DiscoveredApplicationItem) => {
    setLaunchingId(app.app_id);
    setStatusBanner(null);
    setPendingConfirmLaunch(null);

    try {
      const res = await apiClient.launchApplication(app.display_name);
      if (res.ok && res.data) {
        const data: DirectLaunchResponse = res.data;
        if (data.status === 'confirmation_required') {
          setStatusBanner({
            type: 'warning',
            message:
              data.message ||
              `'${app.display_name}' is a sensitive system configuration utility and requires confirmation through the AI Agent.`,
          });
        } else if (data.status === 'ambiguous') {
          setStatusBanner({
            type: 'warning',
            message: data.message,
          });
        } else if (data.success) {
          setStatusBanner({
            type: 'success',
            message:
              data.message || `Successfully launched ${app.display_name}.`,
          });
        } else {
          setStatusBanner({
            type: 'error',
            message: data.message || `Could not launch ${app.display_name}.`,
          });
        }
      } else {
        setStatusBanner({
          type: 'error',
          message: res.error || `Failed to launch ${app.display_name}.`,
        });
      }
    } catch (err: unknown) {
      setStatusBanner({
        type: 'error',
        message:
          err instanceof Error
            ? err.message
            : `Error launching ${app.display_name}.`,
      });
    } finally {
      setLaunchingId(null);
    }
  };

  const handleLaunchClick = (app: DiscoveredApplicationItem) => {
    if (app.requires_confirmation) {
      setPendingConfirmLaunch({
        app,
        message: `'${app.display_name}' is a sensitive Windows system utility. Are you sure you want to open it?`,
      });
      return;
    }
    executeLaunch(app);
  };

  return (
    <div className="applications-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Discovered Windows Applications</h1>
          <p className="page-description">
            Universal Windows 11 application discovery across Start Menu, Registry, Microsoft Store, Program Files, Desktop Shortcuts, Windows Settings, and System Utilities.
          </p>
        </div>

        <button
          className="refresh-btn"
          onClick={handleRefreshIndex}
          disabled={!isBackendConnected || isRefreshing || isLoading}
          type="button"
          aria-label="Refresh application discovery index"
        >
          <RefreshCw size={14} className={isRefreshing ? 'animate-spin' : ''} />
          <span>{isRefreshing ? 'Scanning Windows...' : 'Refresh Index'}</span>
        </button>
      </div>

      {!isBackendConnected ? (
        <ErrorState
          title="Backend Disconnected"
          message="Connect to the local VoxPilot FastAPI backend to browse and launch discovered applications."
          onRetry={() => fetchApplications()}
          isRetrying={isLoading}
        />
      ) : (
        <>
          {/* Summary Bar */}
          <div className="automation-section" style={{ marginBottom: '1rem' }}>
            <div
              className="section-title-row"
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: '0.75rem',
              }}
            >
              <div className="section-title-group">
                <Layers size={18} className="text-primary" />
                <h2 className="section-title">
                  Application Index ({totalCount} Total Indexed)
                </h2>
              </div>
              <div className="text-xs text-muted">
                {lastRefreshedAt
                  ? `Last indexed: ${new Date(lastRefreshedAt).toLocaleString()}`
                  : 'Using active discovery index'}
              </div>
            </div>

            {/* Search & Source Filter Controls */}
            <div
              className="applications-controls-row"
              style={{
                display: 'flex',
                gap: '0.75rem',
                marginTop: '0.75rem',
                flexWrap: 'wrap',
              }}
            >
              <div
                style={{
                  position: 'relative',
                  flex: '1 1 280px',
                  display: 'flex',
                  alignItems: 'center',
                }}
              >
                <Search
                  size={16}
                  style={{
                    position: 'absolute',
                    left: '0.75rem',
                    opacity: 0.6,
                    pointerEvents: 'none',
                  }}
                />
                <input
                  type="text"
                  className="applications-search-input"
                  placeholder="Search installed apps, Store apps, Settings pages, or utilities..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  aria-label="Search applications"
                  style={{
                    width: '100%',
                    padding: '0.55rem 0.75rem 0.55rem 2.25rem',
                    borderRadius: '0.5rem',
                    border: '1px solid var(--border-subtle, #2d3748)',
                    background: 'var(--bg-surface, #151922)',
                    color: 'var(--text-primary, #f7fafc)',
                  }}
                />
              </div>

              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                }}
              >
                <Filter size={15} className="text-muted" />
                <select
                  className="applications-source-select"
                  value={sourceFilter}
                  onChange={(e) => setSourceFilter(e.target.value)}
                  aria-label="Filter by discovery source"
                  style={{
                    padding: '0.55rem 0.75rem',
                    borderRadius: '0.5rem',
                    border: '1px solid var(--border-subtle, #2d3748)',
                    background: 'var(--bg-surface, #151922)',
                    color: 'var(--text-primary, #f7fafc)',
                  }}
                >
                  {SOURCE_OPTIONS.map((opt) => {
                    const count =
                      opt.value === 'all'
                        ? totalCount
                        : bySource[opt.value] ?? 0;
                    return (
                      <option key={opt.value} value={opt.value}>
                        {opt.label} ({count})
                      </option>
                    );
                  })}
                </select>
              </div>
            </div>
          </div>

          {/* Status or Error Banner */}
          {statusBanner && (
            <div
              className={`alert-banner ${
                statusBanner.type === 'success'
                  ? 'alert-success'
                  : statusBanner.type === 'warning'
                  ? 'alert-warning'
                  : 'alert-danger'
              }`}
              role="status"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.6rem',
                padding: '0.75rem 1rem',
                borderRadius: '0.5rem',
                marginBottom: '1rem',
              }}
            >
              {statusBanner.type === 'success' ? (
                <CheckCircle2 size={16} />
              ) : (
                <AlertTriangle size={16} />
              )}
              <span>{statusBanner.message}</span>
            </div>
          )}

          {error && (
            <div
              className="alert-banner alert-warning"
              role="alert"
              style={{ marginBottom: '1rem' }}
            >
              <AlertTriangle size={16} />
              <span>{error}</span>
            </div>
          )}

          {/* Confirmation Prompt for Sensitive System Utilities */}
          {pendingConfirmLaunch && (
            <div
              className="alert-banner alert-warning"
              role="dialog"
              aria-label="Confirm sensitive utility launch"
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '0.85rem 1rem',
                marginBottom: '1rem',
                borderRadius: '0.5rem',
                gap: '1rem',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                <ShieldAlert size={18} />
                <span>{pendingConfirmLaunch.message}</span>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button
                  type="button"
                  className="refresh-btn"
                  onClick={() => executeLaunch(pendingConfirmLaunch.app)}
                >
                  Confirm & Open
                </button>
                <button
                  type="button"
                  className="refresh-btn"
                  onClick={() => setPendingConfirmLaunch(null)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Applications Table / List */}
          <div className="automation-section">
            {isLoading && applications.length === 0 ? (
              <LoadingState message="Loading discovered Windows applications..." />
            ) : applications.length === 0 ? (
              <div className="active-ops-empty">
                <AppWindow size={28} className="text-muted" />
                <div className="empty-text-group">
                  <p className="empty-lead">No matching applications found</p>
                  <p className="empty-sub">
                    Try adjusting your search query, changing the source filter, or clicking Refresh Index.
                  </p>
                </div>
              </div>
            ) : (
              <div className="active-ops-table-container">
                <table className="active-ops-table applications-table">
                  <thead>
                    <tr>
                      <th>Application Name</th>
                      <th>Publisher</th>
                      <th>Discovery Source</th>
                      <th>Launch Method</th>
                      <th style={{ textAlign: 'right' }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {applications.map((app) => (
                      <tr key={app.app_id} className="application-row">
                        <td>
                          <div style={{ fontWeight: 600 }}>{app.display_name}</div>
                          {app.requires_confirmation && (
                            <span
                              className="chip-badge"
                              style={{ fontSize: '0.7rem', marginTop: '0.15rem', display: 'inline-block' }}
                            >
                              Requires Confirmation
                            </span>
                          )}
                        </td>
                        <td className="text-xs text-muted">
                          {app.publisher || 'Local Installation'}
                        </td>
                        <td>
                          <span className="category-pill">
                            {formatSourceLabel(app.source)}
                          </span>
                        </td>
                        <td className="font-mono text-xs">{app.launch_method}</td>
                        <td style={{ textAlign: 'right' }}>
                          <button
                            type="button"
                            className="refresh-btn launch-app-btn"
                            disabled={launchingId === app.app_id}
                            onClick={() => handleLaunchClick(app)}
                            aria-label={`Launch ${app.display_name}`}
                            style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}
                          >
                            <Play size={13} />
                            <span>
                              {launchingId === app.app_id ? 'Launching...' : 'Launch'}
                            </span>
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default ApplicationsPage;

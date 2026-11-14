import React from 'react';
import {
  Palette,
  Sun,
  Moon,
  Monitor,
  RotateCcw,
  Check,
  ShieldCheck,
  AlertTriangle,
} from 'lucide-react';
import {
  ACCENT_PRESETS,
  DEFAULT_ACCENT_COLOR,
  DEFAULT_THEME_MODE,
  useTheme,
} from '../../theme/ThemeProvider';
import { ThemeMode } from '../../api/types';

const THEME_MODES: { id: ThemeMode; label: string; description: string; icon: React.ReactNode }[] = [
  {
    id: 'light',
    label: 'Light',
    description: 'High-contrast bright workspace',
    icon: <Sun size={15} />,
  },
  {
    id: 'dark',
    label: 'Dark',
    description: 'Low-glare dark workspace',
    icon: <Moon size={15} />,
  },
  {
    id: 'system',
    label: 'System',
    description: 'Follow Windows appearance',
    icon: <Monitor size={15} />,
  },
];

export const ThemeSettingsCard: React.FC = () => {
  const {
    themeMode,
    resolvedTheme,
    accentColor,
    isSaving,
    saveError,
    setThemeMode,
    setAccentColor,
    restoreDefaultTheme,
  } = useTheme();

  const isDefault =
    themeMode === DEFAULT_THEME_MODE && accentColor === DEFAULT_ACCENT_COLOR;

  return (
    <div className="settings-card memory-management-card" data-testid="theme-settings-card">
      <div className="settings-card-header">
        <div className="settings-card-title-row">
          <Palette size={18} className="settings-icon" />
          <div>
            <h3>Appearance &amp; Theme</h3>
            <p className="settings-card-subtitle">
              Customize Light, Dark, or System mode and accent color for the active profile
            </p>
          </div>
        </div>
        <div className="memory-header-actions">
          <span className="badge badge-neutral" data-testid="active-theme-badge">
            Active: {resolvedTheme === 'light' ? 'Light' : 'Dark'}
            {themeMode === 'system' ? ' (System)' : ''}
          </span>
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={() => restoreDefaultTheme()}
            disabled={isDefault || isSaving}
            data-testid="restore-default-theme-btn"
          >
            <RotateCcw size={14} />
            <span>Restore Defaults</span>
          </button>
        </div>
      </div>

      <div className="settings-card-body">
        <div className="memory-privacy-notice">
          <ShieldCheck size={15} />
          <span>
            Appearance settings are stored <strong>locally per profile</strong> in SQLite and
            browser cache — no cloud sync or external network requests.
          </span>
        </div>

        {/* Theme Mode Selector */}
        <div className="settings-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 10 }}>
          <div className="settings-row-info">
            <span className="settings-label">Theme Mode</span>
            <span className="settings-hint">
              Changes apply immediately across the entire application without restarting.
            </span>
          </div>

          <div
            role="group"
            aria-label="Theme Mode"
            style={{ display: 'flex', flexWrap: 'wrap', gap: 10, width: '100%' }}
          >
            {THEME_MODES.map((mode) => {
              const selected = themeMode === mode.id;
              return (
                <button
                  key={mode.id}
                  type="button"
                  onClick={() => setThemeMode(mode.id)}
                  aria-pressed={selected}
                  data-testid={`theme-mode-${mode.id}`}
                  className={selected ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '8px 14px',
                    minWidth: 130,
                    justifyContent: 'flex-start',
                  }}
                >
                  {mode.icon}
                  <span>{mode.label}</span>
                  {selected && <Check size={13} style={{ marginLeft: 'auto' }} />}
                </button>
              );
            })}
          </div>
        </div>

        {/* Accent Color Selector */}
        <div className="settings-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 10 }}>
          <div className="settings-row-info">
            <span className="settings-label">Accent Color</span>
            <span className="settings-hint">
              Accessible high-contrast presets for buttons, active states, and focus rings.
            </span>
          </div>

          <div
            role="group"
            aria-label="Accent Color"
            style={{ display: 'flex', flexWrap: 'wrap', gap: 10, width: '100%' }}
          >
            {ACCENT_PRESETS.map((preset) => {
              const selected = accentColor === preset.id;
              const swatchHex =
                resolvedTheme === 'light' ? preset.lightPrimary : preset.darkPrimary;
              return (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => setAccentColor(preset.id)}
                  aria-pressed={selected}
                  aria-label={`Accent color ${preset.label}`}
                  data-testid={`accent-color-${preset.id}`}
                  className="btn-secondary btn-sm"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '7px 12px',
                    borderColor: selected ? swatchHex : undefined,
                    boxShadow: selected ? `0 0 0 1px ${swatchHex}` : undefined,
                    fontWeight: selected ? 600 : 500,
                  }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      width: 14,
                      height: 14,
                      borderRadius: '9999px',
                      backgroundColor: swatchHex,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: '#ffffff',
                      flexShrink: 0,
                    }}
                  >
                    {selected && <Check size={10} />}
                  </span>
                  <span>{preset.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Live Preview */}
        <div
          data-testid="theme-live-preview"
          style={{
            marginTop: 8,
            padding: 12,
            borderRadius: 'var(--radius-sm)',
            backgroundColor: 'var(--bg-surface-elevated)',
            border: '1px solid var(--border-subtle)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 10,
          }}
        >
          <div>
            <div style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: 13 }}>
              Live Theme Preview
            </div>
            <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
              Verifies foreground contrast, borders, and accent controls.
            </div>
          </div>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <span
              style={{
                padding: '3px 9px',
                borderRadius: 9999,
                backgroundColor: 'var(--accent-blue-subtle)',
                color: 'var(--accent-blue)',
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              Accent Badge
            </span>
            <span
              style={{
                padding: '5px 12px',
                borderRadius: 'var(--radius-sm)',
                backgroundColor: 'var(--accent-blue)',
                color: '#ffffff',
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              Primary Action
            </span>
          </div>
        </div>

        {saveError && (
          <div className="settings-banner error" role="alert" style={{ marginTop: 10 }}>
            <AlertTriangle size={15} />
            <span>{saveError}</span>
          </div>
        )}
      </div>
    </div>
  );
};

export default ThemeSettingsCard;

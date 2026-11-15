import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import apiClient from '../api/client';
import { AccentColorId, ProfileItem, ThemeMode } from '../api/types';

export type ResolvedTheme = 'dark' | 'light';
export type MotionIntensity = 'subtle' | 'balanced' | 'expressive';
export type ReducedMotionMode = 'system' | 'on' | 'off';

export interface MotionPreferences {
  animationsEnabled: boolean;
  motionIntensity: MotionIntensity;
  reducedMotionMode: ReducedMotionMode;
  aiCoreAnimationEnabled: boolean;
  voiceOrbEnabled: boolean;
  waveformEnabled: boolean;
}

export const DEFAULT_MOTION_PREFERENCES: MotionPreferences = {
  animationsEnabled: true,
  motionIntensity: 'balanced',
  reducedMotionMode: 'system',
  aiCoreAnimationEnabled: true,
  voiceOrbEnabled: true,
  waveformEnabled: true,
};

export interface AccentPreset {
  id: AccentColorId;
  label: string;
  darkPrimary: string;
  darkHover: string;
  lightPrimary: string;
  lightHover: string;
  subtleRgb: string;
}

export const DEFAULT_THEME_MODE: ThemeMode = 'dark';
export const DEFAULT_ACCENT_COLOR: AccentColorId = 'blue';

export const ACCENT_PRESETS: AccentPreset[] = [
  {
    id: 'blue',
    label: 'VoxPilot Blue',
    darkPrimary: '#3b82f6',
    darkHover: '#2563eb',
    lightPrimary: '#2563eb',
    lightHover: '#1d4ed8',
    subtleRgb: '59, 130, 246',
  },
  {
    id: 'indigo',
    label: 'Indigo',
    darkPrimary: '#6366f1',
    darkHover: '#4f46e5',
    lightPrimary: '#4f46e5',
    lightHover: '#4338ca',
    subtleRgb: '99, 102, 241',
  },
  {
    id: 'emerald',
    label: 'Emerald',
    darkPrimary: '#059669',
    darkHover: '#047857',
    lightPrimary: '#047857',
    lightHover: '#065f46',
    subtleRgb: '5, 150, 105',
  },
  {
    id: 'teal',
    label: 'Teal',
    darkPrimary: '#0d9488',
    darkHover: '#0f766e',
    lightPrimary: '#0f766e',
    lightHover: '#115e59',
    subtleRgb: '13, 148, 136',
  },
  {
    id: 'amber',
    label: 'Amber',
    darkPrimary: '#b45309',
    darkHover: '#92400e',
    lightPrimary: '#b45309',
    lightHover: '#92400e',
    subtleRgb: '180, 83, 9',
  },
  {
    id: 'rose',
    label: 'Rose',
    darkPrimary: '#e11d48',
    darkHover: '#be123c',
    lightPrimary: '#be123c',
    lightHover: '#9f1239',
    subtleRgb: '225, 29, 72',
  },
];

const VALID_MODES = new Set<string>(['dark', 'light', 'system']);
const PRESET_MAP = new Map<string, AccentPreset>(
  ACCENT_PRESETS.map((p) => [p.id, p])
);

const STORAGE_LAST_KEY = 'voxpilot.theme.lastActive';
const storageProfileKey = (profileId: string) => `voxpilot.theme.profile.${profileId}`;
const MOTION_LAST_KEY = 'voxpilot_motion_prefs_v1';
const motionProfileKey = (profileId: string) => `voxpilot_motion_prefs_v1.${profileId}`;

export function resolveEffectiveReducedMotion(
  prefs: MotionPreferences,
  systemPrefersReducedMotion: boolean
): boolean {
  if (!prefs.animationsEnabled) return true;
  if (prefs.reducedMotionMode === 'on') return true;
  if (prefs.reducedMotionMode === 'off') return false;
  return systemPrefersReducedMotion;
}

function getSystemPrefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

export function normalizeMotionPreferences(raw: unknown): MotionPreferences {
  if (!raw || typeof raw !== 'object') {
    return { ...DEFAULT_MOTION_PREFERENCES };
  }
  const obj = raw as Record<string, unknown>;
  const intensity =
    obj.motionIntensity === 'subtle' ||
      obj.motionIntensity === 'balanced' ||
      obj.motionIntensity === 'expressive'
      ? obj.motionIntensity
      : DEFAULT_MOTION_PREFERENCES.motionIntensity;
  const reducedMode =
    obj.reducedMotionMode === 'system' ||
      obj.reducedMotionMode === 'on' ||
      obj.reducedMotionMode === 'off'
      ? obj.reducedMotionMode
      : DEFAULT_MOTION_PREFERENCES.reducedMotionMode;

  return {
    animationsEnabled:
      typeof obj.animationsEnabled === 'boolean'
        ? obj.animationsEnabled
        : DEFAULT_MOTION_PREFERENCES.animationsEnabled,
    motionIntensity: intensity,
    reducedMotionMode: reducedMode,
    aiCoreAnimationEnabled:
      typeof obj.aiCoreAnimationEnabled === 'boolean'
        ? obj.aiCoreAnimationEnabled
        : DEFAULT_MOTION_PREFERENCES.aiCoreAnimationEnabled,
    voiceOrbEnabled:
      typeof obj.voiceOrbEnabled === 'boolean'
        ? obj.voiceOrbEnabled
        : DEFAULT_MOTION_PREFERENCES.voiceOrbEnabled,
    waveformEnabled:
      typeof obj.waveformEnabled === 'boolean'
        ? obj.waveformEnabled
        : DEFAULT_MOTION_PREFERENCES.waveformEnabled,
  };
}

function readLocalMotion(profileId: string): MotionPreferences {
  if (typeof window === 'undefined' || !window.localStorage) {
    return { ...DEFAULT_MOTION_PREFERENCES };
  }
  try {
    const rawProfile = window.localStorage.getItem(motionProfileKey(profileId));
    if (rawProfile) {
      return normalizeMotionPreferences(JSON.parse(rawProfile));
    }
    const rawLast = window.localStorage.getItem(MOTION_LAST_KEY);
    if (rawLast) {
      return normalizeMotionPreferences(JSON.parse(rawLast));
    }
  } catch {
    return { ...DEFAULT_MOTION_PREFERENCES };
  }
  return { ...DEFAULT_MOTION_PREFERENCES };
}

function writeLocalMotion(profileId: string, prefs: MotionPreferences): void {
  if (typeof window === 'undefined' || !window.localStorage) return;
  try {
    const payload = JSON.stringify(prefs);
    window.localStorage.setItem(motionProfileKey(profileId), payload);
    window.localStorage.setItem(MOTION_LAST_KEY, payload);
  } catch {
    // Ignore storage quota errors
  }
}

export function normalizeThemeMode(value: unknown): ThemeMode {
  if (typeof value === 'string') {
    const clean = value.trim().toLowerCase();
    if (VALID_MODES.has(clean)) {
      return clean as ThemeMode;
    }
  }
  return DEFAULT_THEME_MODE;
}

export function normalizeAccentColor(value: unknown): AccentColorId {
  if (typeof value === 'string') {
    const clean = value.trim().toLowerCase();
    if (PRESET_MAP.has(clean)) {
      return clean as AccentColorId;
    }
  }
  return DEFAULT_ACCENT_COLOR;
}

function getSystemPrefersDark(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return true;
  }
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  } catch {
    return true;
  }
}

function readLocalTheme(key: string): { themeMode: ThemeMode; accentColor: AccentColorId } | null {
  if (typeof window === 'undefined' || !window.localStorage) return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      return {
        themeMode: normalizeThemeMode(parsed.themeMode),
        accentColor: normalizeAccentColor(parsed.accentColor),
      };
    }
  } catch {
    return null;
  }
  return null;
}

function writeLocalTheme(
  profileId: string,
  themeMode: ThemeMode,
  accentColor: AccentColorId
): void {
  if (typeof window === 'undefined' || !window.localStorage) return;
  try {
    const payload = JSON.stringify({ themeMode, accentColor });
    window.localStorage.setItem(storageProfileKey(profileId), payload);
    window.localStorage.setItem(STORAGE_LAST_KEY, payload);
  } catch {
    // Ignore storage quota errors
  }
}

export interface ThemeContextValue extends MotionPreferences {
  themeMode: ThemeMode;
  resolvedTheme: ResolvedTheme;
  accentColor: AccentColorId;
  activeProfileId: string;
  isSaving: boolean;
  saveError: string | null;
  effectiveReducedMotion: boolean;
  setThemeMode: (mode: ThemeMode) => Promise<void>;
  setAccentColor: (color: AccentColorId) => Promise<void>;
  restoreDefaultTheme: () => Promise<void>;
  syncProfileTheme: (profile: ProfileItem | null) => void;
  updateMotionPreferences: (updates: Partial<MotionPreferences>) => void;
  resetMotionPreferences: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const initialCached = useMemo(
    () =>
      readLocalTheme(STORAGE_LAST_KEY) || {
        themeMode: DEFAULT_THEME_MODE,
        accentColor: DEFAULT_ACCENT_COLOR,
      },
    []
  );

  const [activeProfileId, setActiveProfileId] = useState<string>('default');
  const [themeMode, setThemeModeState] = useState<ThemeMode>(initialCached.themeMode);
  const [accentColor, setAccentColorState] = useState<AccentColorId>(
    initialCached.accentColor
  );
  const [systemDark, setSystemDark] = useState<boolean>(getSystemPrefersDark);
  const [systemReducedMotion, setSystemReducedMotion] = useState<boolean>(
    getSystemPrefersReducedMotion
  );
  const [motionPrefs, setMotionPrefs] = useState<MotionPreferences>(() =>
    readLocalMotion('default')
  );
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Listen to OS prefers-reduced-motion changes
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setSystemReducedMotion(mq.matches);
    const onMotionChange = (event: MediaQueryListEvent) => {
      setSystemReducedMotion(event.matches);
    };
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', onMotionChange);
      return () => mq.removeEventListener('change', onMotionChange);
    }
    mq.addListener(onMotionChange);
    return () => mq.removeListener(onMotionChange);
  }, []);

  const effectiveReducedMotion = useMemo(
    () => resolveEffectiveReducedMotion(motionPrefs, systemReducedMotion),
    [motionPrefs, systemReducedMotion]
  );

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    root.setAttribute(
      'data-animations',
      motionPrefs.animationsEnabled ? 'enabled' : 'disabled'
    );
    root.setAttribute('data-motion-intensity', motionPrefs.motionIntensity);
    root.setAttribute('data-reduced-motion', String(effectiveReducedMotion));
  }, [motionPrefs, effectiveReducedMotion]);

  // Listen to OS/Windows prefers-color-scheme changes when System mode is selected
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    setSystemDark(mediaQuery.matches);

    const handleChange = (event: MediaQueryListEvent) => {
      setSystemDark(event.matches);
    };

    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', handleChange);
      return () => {
        mediaQuery.removeEventListener('change', handleChange);
      };
    }
    mediaQuery.addListener(handleChange);
    return () => {
      mediaQuery.removeListener(handleChange);
    };
  }, []);

  const resolvedTheme: ResolvedTheme = useMemo(() => {
    if (themeMode === 'system') {
      return systemDark ? 'dark' : 'light';
    }
    return themeMode;
  }, [themeMode, systemDark]);

  // Apply root data-theme attribute and CSS accent tokens immediately
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    root.setAttribute('data-theme', resolvedTheme);

    const preset = PRESET_MAP.get(accentColor) || ACCENT_PRESETS[0];
    const primary = resolvedTheme === 'light' ? preset.lightPrimary : preset.darkPrimary;
    const hover = resolvedTheme === 'light' ? preset.lightHover : preset.darkHover;
    const subtleAlpha = resolvedTheme === 'light' ? '0.11' : '0.14';

    root.style.setProperty('--accent-blue', primary);
    root.style.setProperty('--accent-blue-hover', hover);
    root.style.setProperty(
      '--accent-blue-subtle',
      `rgba(${preset.subtleRgb}, ${subtleAlpha})`
    );
    root.style.setProperty('--border-focus', primary);
  }, [resolvedTheme, accentColor]);

  const persistTheme = useCallback(
    async (
      profileId: string,
      nextMode: ThemeMode,
      nextAccent: AccentColorId
    ) => {
      writeLocalTheme(profileId, nextMode, nextAccent);
      setIsSaving(true);
      setSaveError(null);
      try {
        const res = await apiClient.updateProfile(profileId, {
          theme_mode: nextMode,
          accent_color: nextAccent,
        });
        if (!res.ok) {
          setSaveError(res.error || 'Saved locally; backend profile sync failed.');
        }
      } catch {
        setSaveError('Saved locally; backend unavailable.');
      } finally {
        setIsSaving(false);
      }
    },
    []
  );

  const setThemeMode = useCallback(
    async (mode: ThemeMode) => {
      const normalized = normalizeThemeMode(mode);
      setThemeModeState(normalized);
      await persistTheme(activeProfileId, normalized, accentColor);
    },
    [activeProfileId, accentColor, persistTheme]
  );

  const setAccentColor = useCallback(
    async (color: AccentColorId) => {
      const normalized = normalizeAccentColor(color);
      setAccentColorState(normalized);
      await persistTheme(activeProfileId, themeMode, normalized);
    },
    [activeProfileId, themeMode, persistTheme]
  );

  const restoreDefaultTheme = useCallback(async () => {
    setThemeModeState(DEFAULT_THEME_MODE);
    setAccentColorState(DEFAULT_ACCENT_COLOR);
    await persistTheme(activeProfileId, DEFAULT_THEME_MODE, DEFAULT_ACCENT_COLOR);
  }, [activeProfileId, persistTheme]);

  const syncProfileTheme = useCallback((profile: ProfileItem | null) => {
    if (!profile) return;
    const pid = profile.id || 'default';
    const nextMode = normalizeThemeMode(
      profile.theme_mode ?? readLocalTheme(storageProfileKey(pid))?.themeMode
    );
    const nextAccent = normalizeAccentColor(
      profile.accent_color ?? readLocalTheme(storageProfileKey(pid))?.accentColor
    );
    setActiveProfileId(pid);
    setThemeModeState(nextMode);
    setAccentColorState(nextAccent);
    writeLocalTheme(pid, nextMode, nextAccent);
    setMotionPrefs(readLocalMotion(pid));
    setSaveError(null);
  }, []);

  const updateMotionPreferences = useCallback(
    (updates: Partial<MotionPreferences>) => {
      setMotionPrefs((prev) => {
        const next = normalizeMotionPreferences({ ...prev, ...updates });
        writeLocalMotion(activeProfileId, next);
        return next;
      });
    },
    [activeProfileId]
  );

  const resetMotionPreferences = useCallback(() => {
    const defaults = { ...DEFAULT_MOTION_PREFERENCES };
    setMotionPrefs(defaults);
    writeLocalMotion(activeProfileId, defaults);
  }, [activeProfileId]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      themeMode,
      resolvedTheme,
      accentColor,
      activeProfileId,
      isSaving,
      saveError,
      ...motionPrefs,
      effectiveReducedMotion,
      setThemeMode,
      setAccentColor,
      restoreDefaultTheme,
      syncProfileTheme,
      updateMotionPreferences,
      resetMotionPreferences,
    }),
    [
      themeMode,
      resolvedTheme,
      accentColor,
      activeProfileId,
      isSaving,
      saveError,
      motionPrefs,
      effectiveReducedMotion,
      setThemeMode,
      setAccentColor,
      restoreDefaultTheme,
      syncProfileTheme,
      updateMotionPreferences,
      resetMotionPreferences,
    ]
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
};

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error('useTheme must be used within a ThemeProvider.');
  }
  return ctx;
}

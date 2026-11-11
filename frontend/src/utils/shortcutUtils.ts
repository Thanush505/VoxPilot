/**
 * VoxPilot Frontend - Keyboard Shortcut Formatting & Validation Utilities
 */

/**
 * Format a KeyboardEvent into a canonical shortcut string (e.g. "Ctrl+Shift+V").
 * Returns isComplete=false if only modifier keys are pressed so far.
 */
export function formatKeyboardEventToShortcut(e: {
  key: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
}): { preview: string; isComplete: boolean } {
  const parts: string[] = [];
  if (e.ctrlKey) parts.push('Ctrl');
  if (e.altKey) parts.push('Alt');
  if (e.shiftKey) parts.push('Shift');
  if (e.metaKey) parts.push('Win');

  const rawKey = e.key;
  const modifierNames = ['Control', 'Alt', 'Shift', 'Meta', 'OS'];
  if (modifierNames.includes(rawKey)) {
    return {
      preview: parts.length > 0 ? `${parts.join('+')}+...` : 'Press keys...',
      isComplete: false,
    };
  }

  let keyLabel = rawKey;
  if (rawKey === ' ') {
    keyLabel = 'Space';
  } else if (rawKey === 'Escape' || rawKey === 'Esc') {
    keyLabel = 'Escape';
  } else if (rawKey === 'ArrowUp') {
    keyLabel = 'Up';
  } else if (rawKey === 'ArrowDown') {
    keyLabel = 'Down';
  } else if (rawKey === 'ArrowLeft') {
    keyLabel = 'Left';
  } else if (rawKey === 'ArrowRight') {
    keyLabel = 'Right';
  } else if (rawKey.length === 1) {
    keyLabel = rawKey.toUpperCase();
  } else {
    keyLabel = rawKey.charAt(0).toUpperCase() + rawKey.slice(1);
  }

  parts.push(keyLabel);
  return {
    preview: parts.join('+'),
    isComplete: true,
  };
}

/**
 * Validate a candidate shortcut string on the client side before sending to backend.
 */
export function validateShortcutClientSide(shortcut: string): {
  valid: boolean;
  error: string | null;
} {
  const trimmed = (shortcut || '').trim();
  if (!trimmed || trimmed.endsWith('+...')) {
    return { valid: false, error: 'Please press a complete key combination (e.g., Ctrl+Shift+V).' };
  }

  const tokens = trimmed
    .split('+')
    .map((t) => t.trim())
    .filter(Boolean);

  if (tokens.length === 0) {
    return { valid: false, error: 'Shortcut cannot be empty.' };
  }

  const modifiers = new Set(['CTRL', 'CONTROL', 'ALT', 'SHIFT', 'WIN', 'META']);
  const modTokens = tokens.filter((t) => modifiers.has(t.toUpperCase()));
  const primaryTokens = tokens.filter((t) => !modifiers.has(t.toUpperCase()));

  if (primaryTokens.length === 0) {
    return { valid: false, error: 'Shortcut must include a non-modifier key (e.g., V, M, F9).' };
  }
  if (primaryTokens.length > 1) {
    return { valid: false, error: 'Shortcut can only contain one primary key.' };
  }

  const primaryUpper = primaryTokens[0].toUpperCase();
  const isFunctionKey = /^F([1-9]|1[0-2])$/.test(primaryUpper);
  if (modTokens.length === 0 && !isFunctionKey) {
    return {
      valid: false,
      error: 'Shortcut must include at least one modifier key (Ctrl, Alt, Shift, or Win).',
    };
  }

  const hasCtrl = modTokens.some((m) => m.toUpperCase() === 'CTRL' || m.toUpperCase() === 'CONTROL');
  const hasAlt = modTokens.some((m) => m.toUpperCase() === 'ALT');
  const hasShift = modTokens.some((m) => m.toUpperCase() === 'SHIFT');
  const hasWin = modTokens.some((m) => m.toUpperCase() === 'WIN' || m.toUpperCase() === 'META');

  if (hasCtrl && hasAlt && !hasShift && !hasWin && (primaryUpper === 'ESC' || primaryUpper === 'ESCAPE')) {
    return {
      valid: false,
      error:
        "Shortcut 'Ctrl+Alt+Escape' is permanently reserved for the Global Emergency Stop and cannot be assigned to Voice Mode.",
    };
  }

  return { valid: true, error: null };
}


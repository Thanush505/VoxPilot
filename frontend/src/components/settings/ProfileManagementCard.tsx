import React, { useState, useEffect, useCallback } from 'react';
import {
  Users,
  User,
  Briefcase,
  GraduationCap,
  Code2,
  Home,
  Sparkles,
  Plus,
  RefreshCw,
  ShieldCheck,
  AlertTriangle,
  Trash2,
  Edit3,
  Check,
  X,
} from 'lucide-react';
import apiClient from '../../api/client';
import { ProfileAvatar, ProfileItem } from '../../api/types';

interface ProfileManagementCardProps {
  isBackendConnected: boolean;
  onProfileChanged?: () => void;
}

const AVATAR_OPTIONS: { value: ProfileAvatar; label: string }[] = [
  { value: 'user', label: 'Personal' },
  { value: 'briefcase', label: 'Work' },
  { value: 'graduation-cap', label: 'College' },
  { value: 'code', label: 'Development' },
  { value: 'home', label: 'Home' },
  { value: 'sparkles', label: 'Custom' },
];

export const renderProfileAvatarIcon = (avatar?: string, size = 14) => {
  switch (avatar) {
    case 'briefcase':
      return <Briefcase size={size} />;
    case 'graduation-cap':
      return <GraduationCap size={size} />;
    case 'code':
      return <Code2 size={size} />;
    case 'home':
      return <Home size={size} />;
    case 'sparkles':
      return <Sparkles size={size} />;
    default:
      return <User size={size} />;
  }
};

export const ProfileManagementCard: React.FC<ProfileManagementCardProps> = ({
  isBackendConnected,
  onProfileChanged,
}) => {
  const [profiles, setProfiles] = useState<ProfileItem[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [statusNotice, setStatusNotice] = useState<string | null>(null);

  const [showForm, setShowForm] = useState<boolean>(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formName, setFormName] = useState<string>('');
  const [formDescription, setFormDescription] = useState<string>('');
  const [formAvatar, setFormAvatar] = useState<ProfileAvatar>('user');
  const [formActivate, setFormActivate] = useState<boolean>(true);
  const [isSaving, setIsSaving] = useState<boolean>(false);

  const [activatingId, setActivatingId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadProfiles = useCallback(async () => {
    if (!isBackendConnected) return;
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const res = await apiClient.getProfiles();
      if (res.ok && res.data) {
        setProfiles(res.data.profiles || []);
      } else {
        setErrorMsg(res.error || 'Failed to load local profiles.');
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Unexpected error loading profiles.');
    } finally {
      setIsLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

  const resetForm = () => {
    setEditingId(null);
    setFormName('');
    setFormDescription('');
    setFormAvatar('user');
    setFormActivate(true);
    setShowForm(false);
  };

  const openAddForm = () => {
    resetForm();
    setShowForm(true);
    setErrorMsg(null);
    setStatusNotice(null);
  };

  const openEditForm = (prof: ProfileItem) => {
    setEditingId(prof.id);
    setFormName(prof.name);
    setFormDescription(prof.description || '');
    setFormAvatar((prof.avatar as ProfileAvatar) || 'user');
    setShowForm(true);
    setErrorMsg(null);
    setStatusNotice(null);
  };

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanName = formName.trim();
    if (!cleanName) {
      setErrorMsg('Profile name is required.');
      return;
    }

    setIsSaving(true);
    setErrorMsg(null);
    setStatusNotice(null);
    try {
      if (editingId) {
        const res = await apiClient.updateProfile(editingId, {
          name: cleanName,
          description: formDescription.trim() || null,
          avatar: formAvatar,
        });
        if (res.ok && res.data) {
          setStatusNotice(`Updated profile "${res.data.name}".`);
          resetForm();
          await loadProfiles();
          onProfileChanged?.();
        } else {
          setErrorMsg(res.error || 'Failed to update profile.');
        }
      } else {
        const res = await apiClient.createProfile({
          name: cleanName,
          description: formDescription.trim() || null,
          avatar: formAvatar,
          activate: formActivate,
        });
        if (res.ok && res.data) {
          setStatusNotice(
            `Created profile "${res.data.name}"${res.data.is_active ? ' and switched to it' : ''}.`
          );
          resetForm();
          await loadProfiles();
          onProfileChanged?.();
        } else {
          setErrorMsg(res.error || 'Failed to create profile.');
        }
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Unexpected error saving profile.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleActivate = async (prof: ProfileItem) => {
    setActivatingId(prof.id);
    setErrorMsg(null);
    setStatusNotice(null);
    try {
      const res = await apiClient.activateProfile(prof.id);
      if (res.ok && res.data) {
        setStatusNotice(`Switched active profile to "${res.data.name}".`);
        await loadProfiles();
        onProfileChanged?.();
      } else {
        setErrorMsg(res.error || 'Failed to activate profile.');
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Unexpected error activating profile.');
    } finally {
      setActivatingId(null);
    }
  };

  const handleConfirmDelete = async (profileId: string) => {
    setDeletingId(profileId);
    setErrorMsg(null);
    setStatusNotice(null);
    try {
      const res = await apiClient.deleteProfile(profileId);
      if (res.ok) {
        setConfirmDeleteId(null);
        setStatusNotice('Profile and its isolated local data deleted.');
        await loadProfiles();
        onProfileChanged?.();
      } else {
        setErrorMsg(res.error || 'Failed to delete profile.');
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Unexpected error deleting profile.');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="settings-card memory-management-card" data-testid="profile-management-card">
      <div className="settings-card-header">
        <div className="settings-card-title-row">
          <Users size={18} className="settings-icon" />
          <div>
            <h3>Local Profiles</h3>
            <p className="settings-card-subtitle">
              Separate local workspaces for Personal, Work, College, or Development contexts
            </p>
          </div>
        </div>
        <div className="memory-header-actions">
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={loadProfiles}
            disabled={!isBackendConnected || isLoading}
            title="Refresh profiles"
          >
            <RefreshCw size={14} className={isLoading ? 'spin' : ''} />
            <span>Refresh</span>
          </button>
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => (showForm ? resetForm() : openAddForm())}
            disabled={!isBackendConnected}
            data-testid="add-profile-btn"
          >
            {showForm ? <X size={14} /> : <Plus size={14} />}
            <span>{showForm ? 'Close' : 'New Profile'}</span>
          </button>
        </div>
      </div>

      <div className="settings-card-body">
        <div className="memory-privacy-notice">
          <ShieldCheck size={15} />
          <span>
            Each profile isolates conversations, memories, and preferences{' '}
            <strong>locally in SQLite</strong> (no cloud sync). Local profiles organize
            assistant context and are not operating-system security boundaries.
          </span>
        </div>

        {showForm && (
          <form
            className="memory-add-form"
            onSubmit={handleSaveProfile}
            data-testid="profile-form"
          >
            <div className="memory-add-row">
              <input
                type="text"
                className="memory-add-input"
                placeholder="Profile name (e.g. Work, College, Development)"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                maxLength={64}
                aria-label="Profile Name"
              />
              <select
                className="status-filter-select"
                value={formAvatar}
                onChange={(e) => setFormAvatar(e.target.value as ProfileAvatar)}
                aria-label="Profile Avatar"
              >
                {AVATAR_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    Icon: {opt.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="memory-add-row">
              <input
                type="text"
                className="memory-add-input"
                placeholder="Optional description (e.g. Engineering tasks and coding defaults)"
                value={formDescription}
                onChange={(e) => setFormDescription(e.target.value)}
                maxLength={250}
                aria-label="Profile Description"
              />
              {!editingId && (
                <label className="settings-toggle-label">
                  <input
                    type="checkbox"
                    checked={formActivate}
                    onChange={(e) => setFormActivate(e.target.checked)}
                  />
                  <span>Activate now</span>
                </label>
              )}
              <button
                type="submit"
                className="btn-primary btn-sm"
                disabled={isSaving || !formName.trim()}
                data-testid="save-profile-btn"
              >
                <Check size={14} />
                <span>{isSaving ? 'Saving...' : editingId ? 'Update' : 'Create'}</span>
              </button>
              <button
                type="button"
                className="btn-secondary btn-sm"
                onClick={resetForm}
              >
                <span>Cancel</span>
              </button>
            </div>
          </form>
        )}

        {errorMsg && (
          <div className="settings-banner error" role="alert">
            <AlertTriangle size={15} />
            <span>{errorMsg}</span>
          </div>
        )}

        {statusNotice && !errorMsg && (
          <div className="settings-banner success" role="status">
            <ShieldCheck size={15} />
            <span>{statusNotice}</span>
          </div>
        )}

        {!isBackendConnected ? (
          <div className="memory-empty-state">
            Connect to the local VoxPilot backend to manage profiles.
          </div>
        ) : isLoading && profiles.length === 0 ? (
          <div className="memory-empty-state">Loading local profiles...</div>
        ) : (
          <div className="memory-list">
            {profiles.map((prof) => (
              <div
                key={prof.id}
                className="memory-item"
                data-testid={`profile-item-${prof.id}`}
              >
                <div className="memory-item-main">
                  <div className="memory-item-meta">
                    <span className="memory-type-badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                      {renderProfileAvatarIcon(prof.avatar, 12)}
                      <span>{prof.name}</span>
                    </span>
                    {prof.is_active && (
                      <span className="badge badge-success" style={{ marginLeft: 6 }}>
                        Active
                      </span>
                    )}
                    {prof.is_default && (
                      <span className="badge badge-neutral" style={{ marginLeft: 6 }}>
                        Default
                      </span>
                    )}
                  </div>
                  {prof.description && (
                    <p className="memory-item-content" style={{ marginTop: 4 }}>
                      {prof.description}
                    </p>
                  )}
                </div>

                <div className="memory-item-actions">
                  {!prof.is_active && (
                    <button
                      type="button"
                      className="btn-primary btn-sm"
                      onClick={() => handleActivate(prof)}
                      disabled={activatingId === prof.id}
                      aria-label={`Activate profile ${prof.name}`}
                    >
                      <span>{activatingId === prof.id ? 'Switching...' : 'Switch'}</span>
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => openEditForm(prof)}
                    aria-label={`Edit profile ${prof.name}`}
                  >
                    <Edit3 size={13} />
                    <span>Edit</span>
                  </button>
                  {!prof.is_default && (
                    confirmDeleteId === prof.id ? (
                      <div className="memory-confirm-delete">
                        <button
                          type="button"
                          className="btn-danger btn-sm"
                          onClick={() => handleConfirmDelete(prof.id)}
                          disabled={deletingId === prof.id}
                          aria-label={`Confirm delete profile ${prof.name}`}
                        >
                          {deletingId === prof.id ? 'Deleting...' : 'Confirm Delete'}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary btn-sm"
                          onClick={() => setConfirmDeleteId(null)}
                          disabled={deletingId === prof.id}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="btn-secondary btn-sm memory-delete-btn"
                        onClick={() => setConfirmDeleteId(prof.id)}
                        aria-label={`Delete profile ${prof.name}`}
                      >
                        <Trash2 size={14} />
                        <span>Delete</span>
                      </button>
                    )
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default ProfileManagementCard;

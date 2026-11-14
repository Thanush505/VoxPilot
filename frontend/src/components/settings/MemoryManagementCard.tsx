import React, { useState, useEffect, useCallback } from 'react';
import {
  Brain,
  Search,
  Trash2,
  RefreshCw,
  ShieldCheck,
  AlertTriangle,
  Plus,
  X,
  Database,
} from 'lucide-react';
import apiClient from '../../api/client';
import { MemoryItem, MemoryType } from '../../api/types';

interface MemoryManagementCardProps {
  isBackendConnected: boolean;
}

const MEMORY_TYPE_LABELS: Record<string, string> = {
  user_preference: 'Preference',
  user_fact: 'User Fact',
  instruction: 'Instruction',
  project_context: 'Project Context',
  general_context: 'General Context',
};

const formatMemoryTimestamp = (iso: string | null): string => {
  if (!iso) return 'Unknown date';
  try {
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return iso;
    return dt.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
};

export const MemoryManagementCard: React.FC<MemoryManagementCardProps> = ({
  isBackendConnected,
}) => {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedType, setSelectedType] = useState<string>('all');
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [statusNotice, setStatusNotice] = useState<string | null>(null);

  // Explicit per-row confirmation before deletion
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Optional quick add state
  const [showAddForm, setShowAddForm] = useState<boolean>(false);
  const [newContent, setNewContent] = useState<string>('');
  const [newType, setNewType] = useState<MemoryType>('user_preference');
  const [isCreating, setIsCreating] = useState<boolean>(false);

  const loadMemories = useCallback(async () => {
    if (!isBackendConnected) return;
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const trimmed = searchQuery.trim();
      const typeFilter = selectedType !== 'all' ? selectedType : undefined;
      if (trimmed) {
        const res = await apiClient.searchMemories(trimmed, 50, typeFilter);
        if (res.ok && res.data) {
          setMemories(res.data.memories || []);
          setTotalCount(res.data.count ?? res.data.memories.length);
        } else {
          setErrorMsg(res.error || 'Failed to search local memories.');
        }
      } else {
        const res = await apiClient.getMemories(50, 0, typeFilter);
        if (res.ok && res.data) {
          setMemories(res.data.memories || []);
          setTotalCount(res.data.total ?? res.data.memories.length);
        } else {
          setErrorMsg(res.error || 'Failed to load local memories.');
        }
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Unexpected error loading memories.');
    } finally {
      setIsLoading(false);
    }
  }, [isBackendConnected, searchQuery, selectedType]);

  useEffect(() => {
    const debounce = setTimeout(() => {
      loadMemories();
    }, searchQuery.trim() ? 220 : 0);
    return () => clearTimeout(debounce);
  }, [loadMemories, searchQuery]);

  const handleConfirmDelete = async (memoryId: string) => {
    setDeletingId(memoryId);
    setErrorMsg(null);
    setStatusNotice(null);
    try {
      const res = await apiClient.deleteMemory(memoryId);
      if (res.ok) {
        setMemories((prev) => prev.filter((item) => item.id !== memoryId));
        setTotalCount((prev) => Math.max(0, prev - 1));
        setConfirmDeleteId(null);
        setStatusNotice('Memory deleted from local storage.');
      } else {
        setErrorMsg(res.error || 'Failed to delete memory.');
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Unexpected error deleting memory.');
    } finally {
      setDeletingId(null);
    }
  };

  const handleCreateMemory = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = newContent.trim();
    if (!cleaned) {
      setErrorMsg('Please enter memory content before saving.');
      return;
    }
    setIsCreating(true);
    setErrorMsg(null);
    setStatusNotice(null);
    try {
      const res = await apiClient.createMemory({
        content: cleaned,
        memory_type: newType,
        importance: 4,
      });
      if (res.ok && res.data) {
        setNewContent('');
        setShowAddForm(false);
        setStatusNotice(
          res.data.deduplicated
            ? 'Updated matching existing local memory.'
            : 'Saved new local memory item.'
        );
        await loadMemories();
      } else {
        setErrorMsg(res.error || 'Memory rejected or failed to save.');
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Unexpected error creating memory.');
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="dashboard-card settings-card" data-testid="memory-management-card">
      <div className="dashboard-card-header">
        <div className="dashboard-card-title-group">
          <Brain size={18} className="text-accent-blue" />
          <h2 className="dashboard-card-title">Local AI Memory Management</h2>
          <span className="badge-vault" title="All memories remain on this PC in local SQLite storage.">
            <Database size={12} style={{ marginRight: '4px' }} />
            Stored Locally Only
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            type="button"
            className="refresh-btn"
            onClick={() => {
              setShowAddForm((prev) => !prev);
              setErrorMsg(null);
            }}
            disabled={!isBackendConnected}
          >
            {showAddForm ? <X size={14} /> : <Plus size={14} />}
            <span>{showAddForm ? 'Cancel' : 'Add Memory'}</span>
          </button>
          <button
            type="button"
            className="refresh-btn"
            onClick={loadMemories}
            disabled={!isBackendConnected || isLoading}
          >
            <RefreshCw size={14} className={isLoading ? 'spin' : ''} />
            <span>{isLoading ? 'Loading...' : 'Refresh'}</span>
          </button>
        </div>
      </div>

      <div className="dashboard-card-body">
        <div className="memory-privacy-notice">
          <ShieldCheck size={15} className="text-accent-blue" />
          <span>
            Memories are persisted strictly locally in <code>%LOCALAPPDATA%\VoxPilot</code> with SQLite FTS5.
            Passwords, API keys, tokens, and private keys are automatically rejected and scrubbed.
          </span>
        </div>

        {statusNotice && (
          <div className="ollama-action-notice success" role="status">
            {statusNotice}
          </div>
        )}

        {errorMsg && (
          <div className="panel-error-alert" role="alert">
            <AlertTriangle size={16} />
            <span>{errorMsg}</span>
          </div>
        )}

        {showAddForm && (
          <form className="memory-add-form" onSubmit={handleCreateMemory}>
            <div className="memory-add-controls">
              <input
                type="text"
                className="filter-input memory-content-input"
                value={newContent}
                onChange={(e) => setNewContent(e.target.value)}
                placeholder="e.g., My preferred programming language is Python."
                aria-label="New memory content"
                disabled={isCreating}
              />
              <select
                className="status-filter-select"
                value={newType}
                onChange={(e) => setNewType(e.target.value as MemoryType)}
                aria-label="New memory type"
                disabled={isCreating}
              >
                <option value="user_preference">Preference</option>
                <option value="user_fact">User Fact</option>
                <option value="instruction">Instruction</option>
                <option value="project_context">Project Context</option>
                <option value="general_context">General Context</option>
              </select>
              <button
                type="submit"
                className="state-action-btn"
                style={{ marginTop: 0, padding: '7px 14px' }}
                disabled={!isBackendConnected || isCreating}
              >
                <span>{isCreating ? 'Saving...' : 'Save Memory'}</span>
              </button>
            </div>
          </form>
        )}

        <div className="memory-toolbar">
          <div className="memory-search-box">
            <Search size={14} className="text-muted" />
            <input
              type="text"
              className="memory-search-input"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search memories (FTS5 local search)..."
              aria-label="Search local memories"
              disabled={!isBackendConnected}
            />
            {searchQuery && (
              <button
                type="button"
                className="btn-text-muted"
                onClick={() => setSearchQuery('')}
                aria-label="Clear memory search"
              >
                Clear
              </button>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <select
              className="status-filter-select"
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value)}
              aria-label="Filter memories by type"
              disabled={!isBackendConnected}
            >
              <option value="all">All Memory Types</option>
              <option value="user_preference">Preferences</option>
              <option value="user_fact">User Facts</option>
              <option value="instruction">Instructions</option>
              <option value="project_context">Project Context</option>
              <option value="general_context">General Context</option>
            </select>
            <span className="record-count-text">
              {totalCount} {totalCount === 1 ? 'memory' : 'memories'}
            </span>
          </div>
        </div>

        {!isBackendConnected ? (
          <div className="ollama-empty-note">
            Connect to the local VoxPilot backend to inspect or manage saved AI memories.
          </div>
        ) : isLoading && memories.length === 0 ? (
          <div className="ollama-empty-note">Loading local AI memories...</div>
        ) : memories.length === 0 ? (
          <div className="ollama-empty-note">
            {searchQuery.trim()
              ? `No local memories matched "${searchQuery.trim()}".`
              : 'No persistent memories saved yet. Explicit statements like "I prefer dark mode" or "My project is called VoxPilot" are remembered locally.'}
          </div>
        ) : (
          <div className="memory-list">
            {memories.map((item) => {
              const isConfirming = confirmDeleteId === item.id;
              const isDeleting = deletingId === item.id;
              const typeLabel = MEMORY_TYPE_LABELS[item.memory_type] || item.memory_type;
              return (
                <div key={item.id} className="memory-item-row" data-testid="memory-item-row">
                  <div className="memory-item-main">
                    <div className="memory-item-content">{item.content}</div>
                    <div className="memory-item-meta">
                      <span className={`category-pill memory-type-${item.memory_type}`}>
                        {typeLabel}
                      </span>
                      <span>Importance: {item.importance}/5</span>
                      <span>Updated: {formatMemoryTimestamp(item.updated_at)}</span>
                    </div>
                  </div>
                  <div className="memory-item-actions">
                    {isConfirming ? (
                      <div className="memory-confirm-delete-group">
                        <button
                          type="button"
                          className="btn-model-action unload"
                          onClick={() => handleConfirmDelete(item.id)}
                          disabled={isDeleting}
                          aria-label={`Confirm delete memory ${item.id}`}
                        >
                          <Trash2 size={13} />
                          <span>{isDeleting ? 'Deleting...' : 'Confirm Delete'}</span>
                        </button>
                        <button
                          type="button"
                          className="refresh-btn"
                          onClick={() => setConfirmDeleteId(null)}
                          disabled={isDeleting}
                        >
                          <span>Cancel</span>
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="refresh-btn memory-delete-btn"
                        onClick={() => setConfirmDeleteId(item.id)}
                        aria-label={`Delete memory ${item.id}`}
                        title="Delete saved memory"
                      >
                        <Trash2 size={14} />
                        <span>Delete</span>
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default MemoryManagementCard;

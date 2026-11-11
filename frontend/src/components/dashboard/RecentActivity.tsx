import React, { useState, useEffect } from 'react';
import { History, MessageSquare, Calendar, RefreshCw } from 'lucide-react';
import apiClient from '../../api/client';
import { ConversationInfo } from '../../api/types';
import EmptyState from '../common/EmptyState';
import LoadingState from '../common/LoadingState';

interface RecentActivityProps {
  isBackendConnected: boolean;
}

export const RecentActivity: React.FC<RecentActivityProps> = ({ isBackendConnected }) => {
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConversations = async () => {
    if (!isBackendConnected) {
      setConversations([]);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const res = await apiClient.getConversations(5, 0);
      if (res.ok && res.data) {
        setConversations(res.data);
      } else {
        setError(res.error || 'Failed to query conversations.');
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to query conversation history.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchConversations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBackendConnected]);

  const formatDate = (isoString: string | null): string => {
    if (!isoString) return 'Unknown date';
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString([], {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  };

  return (
    <div className="dashboard-card">
      <div className="dashboard-card-header">
        <div className="dashboard-card-title-group">
          <History size={18} />
          <div>
            <h2 className="dashboard-card-title">Recent Activity</h2>
            <p className="dashboard-card-description">
              Recent conversation sessions and agent execution records from SQLite.
            </p>
          </div>
        </div>

        {isBackendConnected && (
          <button
            className="refresh-btn"
            onClick={fetchConversations}
            disabled={isLoading}
            type="button"
          >
            <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
            <span>Reload</span>
          </button>
        )}
      </div>

      {!isBackendConnected ? (
        <EmptyState
          icon={<History size={24} />}
          title="Backend Not Connected"
          description="Connect the VoxPilot FastAPI backend service to inspect persistent SQLite conversation and execution history."
        />
      ) : isLoading ? (
        <LoadingState
          title="Loading Activity History..."
          message="Querying recent conversation records from SQLite repository."
        />
      ) : error ? (
        <EmptyState
          icon={<History size={24} />}
          title="Unable to Retrieve History"
          description={`Error querying backend: ${error}`}
        />
      ) : conversations.length === 0 ? (
        <EmptyState
          icon={<History size={24} />}
          title="No Recent Activity Records"
          description="No recent conversation sessions were found in SQLite storage. Start a voice or text command session to record executions."
        />
      ) : (
        <div className="activity-list">
          {conversations.map((c) => (
            <div key={c.conversation_id} className="activity-item">
              <div className="activity-info">
                <div className="activity-title">
                  {c.title || `Conversation ${c.conversation_id.slice(0, 8)}...`}
                </div>
                {c.summary && <div className="activity-summary">{c.summary}</div>}
              </div>
              <div className="activity-meta">
                <Calendar size={13} />
                <span>{formatDate(c.updated_at || c.created_at)}</span>
                <MessageSquare size={13} style={{ marginLeft: '8px' }} />
                <span>Session</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default RecentActivity;


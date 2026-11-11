import React from 'react';
import { Plus, MessageSquare, History } from 'lucide-react';
import { ConversationInfo } from '../../api/types';

interface ConversationListProps {
  conversations: ConversationInfo[];
  currentConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  isLoading: boolean;
}

export const ConversationList: React.FC<ConversationListProps> = ({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  isLoading,
}) => {
  const formatDate = (isoString: string | null) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
    } catch {
      return '';
    }
  };

  return (
    <aside className="conversation-panel">
      <div className="conversation-panel-header">
        <div className="panel-title-row">
          <History size={16} />
          <span>Sessions</span>
        </div>
        <button
          className="new-session-button"
          onClick={onNewConversation}
          title="Start fresh conversation"
          type="button"
        >
          <Plus size={14} />
          <span>New Session</span>
        </button>
      </div>

      <div className="conversation-items-list">
        {isLoading && conversations.length === 0 ? (
          <div className="loading-sessions-hint">Loading sessions...</div>
        ) : conversations.length === 0 ? (
          <div className="no-sessions-hint">No sessions yet.</div>
        ) : (
          conversations.map((conv) => {
            const isSelected = conv.conversation_id === currentConversationId;
            return (
              <button
                key={conv.conversation_id}
                className={`conversation-item ${isSelected ? 'active' : ''}`}
                onClick={() => onSelectConversation(conv.conversation_id)}
                type="button"
              >
                <MessageSquare size={14} className="session-icon" />
                <div className="session-text-group">
                  <span className="session-title">
                    {conv.title || `Session ${conv.conversation_id.slice(0, 8)}`}
                  </span>
                  {conv.summary && (
                    <span className="session-summary">{conv.summary}</span>
                  )}
                </div>
                <span className="session-date">
                  {formatDate(conv.updated_at || conv.created_at)}
                </span>
              </button>
            );
          })
        )}
      </div>
    </aside>
  );
};

export default ConversationList;


import React, { useState } from 'react';
import { Send, Edit3, CheckCircle2, XCircle } from 'lucide-react';
import apiClient from '../../api/client';
import type { EmailDraftItem } from '../../api/types';

interface EmailDraftPanelProps {
  draft: EmailDraftItem;
  profileId?: string;
  onDraftUpdated?: (draft: EmailDraftItem) => void;
  onSent?: (message: string) => void;
}

export const EmailDraftPanel: React.FC<EmailDraftPanelProps> = ({
  draft,
  profileId = 'default',
  onDraftUpdated,
  onSent,
}) => {
  const [toText, setToText] = useState(draft.to.join(', '));
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [pendingToken, setPendingToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [statusNote, setStatusNote] = useState<string | null>(null);

  const handleSaveEdits = async () => {
    setBusy(true);
    setStatusNote(null);
    try {
      const toList = toText
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await apiClient.updateEmailDraft(draft.draft_id, {
        profile_id: profileId,
        to: toList,
        subject,
        body,
      });
      if (res.ok && res.data) {
        onDraftUpdated?.(res.data.draft);
        setStatusNote('Draft changes saved.');
      } else {
        setStatusNote(res.error || 'Could not update draft.');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleRequestSend = async () => {
    setBusy(true);
    setStatusNote(null);
    try {
      await handleSaveEdits();
      const res = await apiClient.requestEmailSendConfirmation(draft.draft_id, profileId);
      if (res.ok && res.data?.confirmation_token) {
        setPendingToken(res.data.confirmation_token);
      } else {
        setStatusNote(res.error || 'Could not prepare send confirmation.');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleConfirmSend = async () => {
    if (!pendingToken) return;
    setBusy(true);
    setStatusNote(null);
    try {
      const res = await apiClient.confirmAndSendEmail(draft.draft_id, {
        profile_id: profileId,
        confirmation_token: pendingToken,
        approved: true,
      });
      if (res.ok && res.data?.success) {
        setPendingToken(null);
        setStatusNote(res.data.message);
        onSent?.(res.data.message);
      } else {
        setStatusNote(res.error || res.data?.message || 'Send failed.');
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="settings-card" style={{ marginTop: '10px', padding: '12px' }}>
      <div className="settings-card-header" style={{ marginBottom: '8px' }}>
        <div className="settings-card-title">
          <Edit3 size={16} />
          <strong>Editable Email Draft</strong>
        </div>
        <span className="badge">{draft.status}</span>
      </div>

      <div style={{ display: 'grid', gap: '8px' }}>
        <input
          type="text"
          className="form-input"
          placeholder="Recipient email(s), comma-separated"
          value={toText}
          onChange={(e) => setToText(e.target.value)}
          disabled={!!pendingToken || draft.status === 'sent'}
        />
        <input
          type="text"
          className="form-input"
          placeholder="Subject"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          disabled={!!pendingToken || draft.status === 'sent'}
        />
        <textarea
          className="form-input"
          rows={5}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          disabled={!!pendingToken || draft.status === 'sent'}
        />
      </div>

      {statusNote && (
        <div style={{ marginTop: '8px', fontSize: '0.85rem' }}>{statusNote}</div>
      )}

      {pendingToken ? (
        <div
          style={{
            marginTop: '10px',
            padding: '10px',
            borderRadius: '8px',
            border: '1px solid var(--border-color, #444)',
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: '6px' }}>
            Final Send Confirmation — Verify exact message before sending:
          </div>
          <div style={{ fontSize: '0.85rem', marginBottom: '8px' }}>
            <div><strong>To:</strong> {toText}</div>
            <div><strong>Subject:</strong> {subject}</div>
            <div style={{ whiteSpace: 'pre-wrap', marginTop: '4px' }}>{body}</div>
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              type="button"
              className="state-action-btn"
              onClick={handleConfirmSend}
              disabled={busy}
            >
              <CheckCircle2 size={14} />
              <span>{busy ? 'Sending...' : 'Approve & Send'}</span>
            </button>
            <button
              type="button"
              className="refresh-btn"
              onClick={() => setPendingToken(null)}
              disabled={busy}
            >
              <XCircle size={14} />
              <span>Cancel</span>
            </button>
          </div>
        </div>
      ) : (
        draft.status !== 'sent' && (
          <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
            <button
              type="button"
              className="refresh-btn"
              onClick={handleSaveEdits}
              disabled={busy}
            >
              <span>Save Draft</span>
            </button>
            <button
              type="button"
              className="state-action-btn"
              onClick={handleRequestSend}
              disabled={busy}
            >
              <Send size={14} />
              <span>Review & Send</span>
            </button>
          </div>
        )
      )}
    </div>
  );
};

export default EmailDraftPanel;

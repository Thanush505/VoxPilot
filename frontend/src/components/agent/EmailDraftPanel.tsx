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
    <div className="vp-email-draft-card" data-testid="email-draft-panel">
      <div className="vp-email-draft-header">
        <div className="settings-card-title">
          <Edit3 size={16} />
          <strong>Editable Email Draft</strong>
        </div>
        <span className="vp-inline-badge">{draft.status}</span>
      </div>

      <div className="vp-email-draft-grid">
        <input
          type="text"
          className="vp-provider-input"
          placeholder="Recipient email(s), comma-separated"
          value={toText}
          onChange={(e) => setToText(e.target.value)}
          disabled={!!pendingToken || draft.status === 'sent'}
        />
        <input
          type="text"
          className="vp-provider-input"
          placeholder="Subject"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          disabled={!!pendingToken || draft.status === 'sent'}
        />
      </div>

      <textarea
        className="vp-provider-input"
        rows={5}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        disabled={!!pendingToken || draft.status === 'sent'}
      />

      {statusNote && (
        <div className="vp-inline-meta-bar">{statusNote}</div>
      )}

      {pendingToken ? (
        <div className="vp-provider-section-card">
          <div style={{ fontWeight: 600, fontSize: 13 }}>
            Final Send Confirmation — Verify exact message before sending:
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
            <div><strong>To:</strong> {toText}</div>
            <div><strong>Subject:</strong> {subject}</div>
            <div style={{ whiteSpace: 'pre-wrap', marginTop: 6 }}>{body}</div>
          </div>
          <div className="vp-provider-actions-row">
            <button
              type="button"
              className="vp-btn-primary"
              onClick={handleConfirmSend}
              disabled={busy}
            >
              <CheckCircle2 size={14} />
              <span>{busy ? 'Sending...' : 'Approve & Send'}</span>
            </button>
            <button
              type="button"
              className="vp-btn-secondary"
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
          <div className="vp-provider-actions-row">
            <button
              type="button"
              className="vp-btn-secondary"
              onClick={handleSaveEdits}
              disabled={busy}
            >
              <span>Save Draft</span>
            </button>
            <button
              type="button"
              className="vp-btn-primary"
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

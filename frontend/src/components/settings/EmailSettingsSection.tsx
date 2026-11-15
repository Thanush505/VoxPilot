import React, { useEffect, useState } from 'react';
import { Mail, Lock, Trash2, CheckCircle2 } from 'lucide-react';
import apiClient from '../../api/client';
import type { EmailAccountPublicConfig } from '../../api/types';

interface EmailSettingsSectionProps {
  activeProfileId?: string;
}

export const EmailSettingsSection: React.FC<EmailSettingsSectionProps> = ({
  activeProfileId = 'default',
}) => {
  const [connected, setConnected] = useState(false);
  const [account, setAccount] = useState<EmailAccountPublicConfig | null>(null);
  const [emailAddress, setEmailAddress] = useState('');
  const [password, setPassword] = useState('');
  const [imapHost, setImapHost] = useState('imap.gmail.com');
  const [imapPort, setImapPort] = useState(993);
  const [smtpHost, setSmtpHost] = useState('smtp.gmail.com');
  const [smtpPort, setSmtpPort] = useState(465);
  const [useStarttls, setUseStarttls] = useState(false);
  const [authorized, setAuthorized] = useState(false);
  const [saving, setSaving] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const loadAccount = async () => {
    const res = await apiClient.getEmailAccountStatus(activeProfileId);
    if (res.ok && res.data) {
      setConnected(res.data.connected);
      setAccount(res.data.account);
      if (res.data.account) {
        setEmailAddress(res.data.account.email_address);
        setImapHost(res.data.account.imap_host);
        setImapPort(res.data.account.imap_port);
        setSmtpHost(res.data.account.smtp_host);
        setSmtpPort(res.data.account.smtp_port);
        setUseStarttls(res.data.account.use_starttls);
      }
    }
  };

  useEffect(() => {
    loadAccount();
  }, [activeProfileId]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatusMsg(null);
    if (!authorized) {
      setStatusMsg({
        type: 'error',
        text: 'Please check the authorization box to confirm connecting this email account.',
      });
      return;
    }
    setSaving(true);
    try {
      const res = await apiClient.configureEmailAccount({
        profile_id: activeProfileId,
        email_address: emailAddress,
        password,
        imap_host: imapHost,
        imap_port: imapPort,
        smtp_host: smtpHost,
        smtp_port: smtpPort,
        use_starttls: useStarttls,
        authorized_by_user: true,
      });
      if (res.ok && res.data) {
        setConnected(res.data.connected);
        setAccount(res.data.account);
        setPassword('');
        setAuthorized(false);
        setStatusMsg({
          type: 'success',
          text: 'Email account connected securely using Windows DPAPI encryption.',
        });
      } else {
        setStatusMsg({ type: 'error', text: res.error || 'Failed to save email account.' });
      }
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    setSaving(true);
    setStatusMsg(null);
    try {
      const res = await apiClient.disconnectEmailAccount(activeProfileId);
      if (res.ok) {
        setConnected(false);
        setAccount(null);
        setPassword('');
        setStatusMsg({ type: 'success', text: 'Email account disconnected for this profile.' });
      } else {
        setStatusMsg({ type: 'error', text: res.error || 'Failed to disconnect account.' });
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-card" style={{ marginTop: '16px' }}>
      <div className="settings-card-header">
        <div className="settings-card-title">
          <Mail size={18} />
          <h3>Email Assistant Account (IMAP / SMTP)</h3>
        </div>
        <span className={`status-pill ${connected ? 'success' : 'neutral'}`}>
          {connected ? 'Connected' : 'Not Connected'}
        </span>
      </div>
      <p className="settings-description">
        Connect an authorized email account for profile <code>{activeProfileId}</code> using an App Password.
        Credentials are encrypted locally with Windows DPAPI and isolated per profile.
      </p>

      {statusMsg && (
        <div
          className={`feedback-banner ${statusMsg.type}`}
          style={{ marginBottom: '12px' }}
        >
          {statusMsg.text}
        </div>
      )}

      {connected && account ? (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
          <div>
            <div>
              <CheckCircle2 size={14} style={{ display: 'inline', marginRight: '6px' }} />
              <strong>{account.email_address}</strong>
            </div>
            <div style={{ fontSize: '0.85rem', opacity: 0.8 }}>
              IMAP: {account.imap_host}:{account.imap_port} | SMTP: {account.smtp_host}:{account.smtp_port}
            </div>
          </div>
          <button
            type="button"
            className="vp-btn-secondary"
            onClick={handleDisconnect}
            disabled={saving}
          >
            <Trash2 size={14} />
            <span>Disconnect</span>
          </button>
        </div>
      ) : (
        <form onSubmit={handleSave} style={{ display: 'grid', gap: '10px' }}>
          <input
            type="email"
            className="form-input"
            placeholder="Email address (e.g. you@gmail.com)"
            value={emailAddress}
            onChange={(e) => setEmailAddress(e.target.value)}
            required
          />
          <input
            type="password"
            className="form-input"
            placeholder="App Password (stored locally via DPAPI)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '8px' }}>
            <input
              type="text"
              className="form-input"
              placeholder="IMAP Host"
              value={imapHost}
              onChange={(e) => setImapHost(e.target.value)}
              required
            />
            <input
              type="number"
              className="form-input"
              placeholder="IMAP Port"
              value={imapPort}
              onChange={(e) => setImapPort(Number(e.target.value) || 993)}
              required
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '8px' }}>
            <input
              type="text"
              className="form-input"
              placeholder="SMTP Host"
              value={smtpHost}
              onChange={(e) => setSmtpHost(e.target.value)}
              required
            />
            <input
              type="number"
              className="form-input"
              placeholder="SMTP Port"
              value={smtpPort}
              onChange={(e) => setSmtpPort(Number(e.target.value) || 465)}
              required
            />
          </div>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={useStarttls}
              onChange={(e) => setUseStarttls(e.target.checked)}
            />
            <span>Use STARTTLS for SMTP (e.g. port 587)</span>
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={authorized}
              onChange={(e) => setAuthorized(e.target.checked)}
            />
            <span>I authorize VoxPilot to access this mailbox for this profile only.</span>
          </label>
          <button type="submit" className="vp-btn-primary" disabled={saving}>
            <Lock size={14} />
            <span>{saving ? 'Saving...' : 'Connect Email Account'}</span>
          </button>
        </form>
      )}
    </div>
  );
};

export default EmailSettingsSection;

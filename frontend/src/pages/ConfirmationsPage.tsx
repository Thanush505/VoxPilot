import React, { useState, useEffect, useCallback } from 'react';
import {
  ShieldCheck,
  RefreshCw,
  Lock,
} from 'lucide-react';
import apiClient from '../api/client';
import {
  PendingConfirmationInfo,
} from '../api/types';
import ConfirmationCard from '../components/confirmations/ConfirmationCard';
import ConfirmationDialog from '../components/confirmations/ConfirmationDialog';
import LoadingState from '../components/common/LoadingState';
import EmptyState from '../components/common/EmptyState';
import ErrorState from '../components/common/ErrorState';

interface ConfirmationsPageProps {
  isBackendConnected: boolean;
}

export const ConfirmationsPage: React.FC<ConfirmationsPageProps> = ({ isBackendConnected }) => {
  const [pendingList, setPendingList] = useState<PendingConfirmationInfo[]>([]);
  const [selectedConf, setSelectedConf] = useState<PendingConfirmationInfo | null>(null);
  const [actionType, setActionType] = useState<'approve' | 'reject'>('approve');
  const [isDialogOpen, setIsDialogOpen] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isActionLoading, setIsActionLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [successNotice, setSuccessNotice] = useState<string | null>(null);

  // Discover all executions that currently require confirmation
  const fetchPendingConfirmations = useCallback(async () => {
    if (!isBackendConnected) {
      setPendingList([]);
      return;
    }

    setIsLoading(true);
    setErrorMsg(null);
    setSuccessNotice(null);

    try {
      const convRes = await apiClient.getConversations(20, 0);
      if (!convRes.ok || !convRes.data) {
        setErrorMsg(convRes.error || 'Failed to list conversations.');
        setIsLoading(false);
        return;
      }

      const foundConfirmations: PendingConfirmationInfo[] = [];

      for (const conv of convRes.data) {
        const execsRes = await apiClient.getConversationExecutions(conv.conversation_id);
        if (execsRes.ok && execsRes.data) {
          for (const exec of execsRes.data) {
            if (exec.execution_status === 'confirmation_required') {
              // Try fetching exact pending confirmation object
              const confRes = await apiClient.getPendingConfirmation(exec.execution_id);
              if (confRes.ok && confRes.data) {
                foundConfirmations.push({
                  ...confRes.data,
                  execution_id: exec.execution_id,
                });
              } else if (exec.pending_confirmation) {
                foundConfirmations.push({
                  ...exec.pending_confirmation,
                  execution_id: exec.execution_id,
                });
              }
            }
          }
        }
      }

      // Deduplicate by token / execution_id
      const unique = new Map<string, PendingConfirmationInfo>();
      foundConfirmations.forEach((item) => {
        if (item.execution_id) {
          unique.set(item.execution_id, item);
        }
      });

      setPendingList(Array.from(unique.values()));
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Error fetching pending confirmations.');
    } finally {
      setIsLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    fetchPendingConfirmations();
  }, [fetchPendingConfirmations]);

  const handleOpenApprove = (conf: PendingConfirmationInfo) => {
    setSelectedConf(conf);
    setActionType('approve');
    setActionError(null);
    setIsDialogOpen(true);
  };

  const handleOpenReject = (conf: PendingConfirmationInfo) => {
    setSelectedConf(conf);
    setActionType('reject');
    setActionError(null);
    setIsDialogOpen(true);
  };

  const handleConfirmAction = async (reason?: string) => {
    if (!selectedConf || !selectedConf.execution_id) return;

    setIsActionLoading(true);
    setActionError(null);

    try {
      if (actionType === 'approve') {
        const res = await apiClient.approveConfirmation(
          selectedConf.execution_id,
          selectedConf.token
        );

        if (res.ok) {
          setSuccessNotice(`Action '${selectedConf.action}' approved. Agent resumed execution.`);
          setIsDialogOpen(false);
          setSelectedConf(null);
          fetchPendingConfirmations();
        } else {
          setActionError(res.error || 'Failed to approve confirmation token.');
        }
      } else {
        const res = await apiClient.rejectConfirmation(
          selectedConf.execution_id,
          selectedConf.token,
          reason
        );

        if (res.ok) {
          setSuccessNotice(`Action '${selectedConf.action}' rejected. Remaining workflow cancelled.`);
          setIsDialogOpen(false);
          setSelectedConf(null);
          fetchPendingConfirmations();
        } else {
          setActionError(res.error || 'Failed to reject confirmation.');
        }
      }
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : 'Error during confirmation request.');
    } finally {
      setIsActionLoading(false);
    }
  };

  return (
    <div className="confirmations-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Human-in-the-Loop Confirmation Center</h1>
          <p className="page-description">
            Explicit security review for pending destructive, irreversible, or consequential automation actions.
          </p>
        </div>

        <button
          className="refresh-btn"
          onClick={fetchPendingConfirmations}
          disabled={isLoading}
          type="button"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          <span>Refresh Queue</span>
        </button>
      </div>

      {/* Prominent Development Security Boundary Notice */}
      <div className="security-boundary-banner">
        <div className="banner-icon">
          <Lock size={20} />
        </div>
        <div className="banner-text">
          <span className="banner-title">Development Authentication Trust Boundary</span>
          <p className="banner-desc">
            This confirmation approval interface operates under a local desktop trust boundary
            (localhost binding <code>127.0.0.1</code> and Origin header verification). It is designed to
            protect against cross-origin browser scripting (CSRF) and LLM self-approval, but does not provide
            multi-user cryptographic identity authentication. In production deployments, hardware security keys
            or cryptographic biometric sign-off will be integrated before approving destructive operations.
          </p>
        </div>
      </div>

      {successNotice && (
        <div className="action-success-notice">
          <ShieldCheck size={16} />
          <span>{successNotice}</span>
        </div>
      )}

      {!isBackendConnected ? (
        <ErrorState
          title="Backend Disconnected"
          message="FastAPI service is offline. Cannot check pending human confirmations."
          onRetry={fetchPendingConfirmations}
          isRetrying={isLoading}
        />
      ) : isLoading ? (
        <LoadingState
          title="Checking Pending Confirmations..."
          message="Scanning execution sessions for pending approval tokens."
        />
      ) : errorMsg ? (
        <ErrorState
          title="Error Querying Confirmation Queue"
          message={errorMsg}
          onRetry={fetchPendingConfirmations}
          isRetrying={isLoading}
        />
      ) : pendingList.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck size={32} style={{ color: 'var(--status-success)' }} />}
          title="No Pending Confirmations"
          description="All clear. No automation actions currently require user review or authorization. Any future consequential actions (e.g. deleting files, web form submissions) will pause and appear here."
        />
      ) : (
        <div className="confirmations-grid">
          {pendingList.map((item) => (
            <ConfirmationCard
              key={item.execution_id || item.token}
              confirmation={item}
              onApproveClick={handleOpenApprove}
              onRejectClick={handleOpenReject}
            />
          ))}
        </div>
      )}

      {/* Modal Confirmation Dialog */}
      <ConfirmationDialog
        isOpen={isDialogOpen}
        confirmation={selectedConf}
        actionType={actionType}
        onConfirm={handleConfirmAction}
        onCancel={() => setIsDialogOpen(false)}
        isLoading={isActionLoading}
        errorMessage={actionError}
      />
    </div>
  );
};

export default ConfirmationsPage;

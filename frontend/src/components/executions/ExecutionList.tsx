import React, { useState } from 'react';
import {
  Layers,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ZapOff,
  Filter,
  Eye,
  Clock,
} from 'lucide-react';
import { ExecutionStatusResponse } from '../../api/types';
import EmptyState from '../common/EmptyState';

interface ExecutionListProps {
  executions: ExecutionStatusResponse[];
  onSelectExecution: (execution: ExecutionStatusResponse) => void;
  isLoading: boolean;
}

export const ExecutionList: React.FC<ExecutionListProps> = ({
  executions,
  onSelectExecution,
  isLoading,
}) => {
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const filtered = statusFilter === 'all'
    ? executions
    : executions.filter((e) => e.execution_status === statusFilter);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'completed':
        return (
          <span className="health-pill connected">
            <CheckCircle2 size={12} />
            <span>Completed</span>
          </span>
        );
      case 'failed':
        return (
          <span className="health-pill disconnected">
            <XCircle size={12} />
            <span>Failed</span>
          </span>
        );
      case 'cancelled':
        return (
          <span className="health-pill disconnected" style={{ backgroundColor: 'var(--status-inactive-bg)', color: 'var(--status-inactive)' }}>
            <ZapOff size={12} />
            <span>Cancelled</span>
          </span>
        );
      case 'confirmation_required':
        return (
          <span className="health-pill checking" style={{ backgroundColor: 'var(--status-warning-bg)', color: 'var(--status-warning)' }}>
            <AlertTriangle size={12} />
            <span>Needs Approval</span>
          </span>
        );
      case 'rate_limited':
        return (
          <span className="health-pill disconnected">
            <AlertTriangle size={12} />
            <span>Rate Limited</span>
          </span>
        );
      default:
        return (
          <span className="health-pill checking">
            <Clock size={12} />
            <span style={{ textTransform: 'capitalize' }}>{status}</span>
          </span>
        );
    }
  };

  if (executions.length === 0 && !isLoading) {
    return (
      <EmptyState
        icon={<Layers size={24} />}
        title="No Execution Records Found"
        description="No execution sessions were found in SQLite. When the agent receives natural language or voice commands, execution history will appear here."
      />
    );
  }

  return (
    <div className="execution-list-container">
      {/* Filters Toolbar */}
      <div className="execution-toolbar">
        <div className="toolbar-left">
          <Filter size={14} className="text-muted" />
          <span className="toolbar-label">Status Filter:</span>
          <select
            className="status-filter-select"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="all">All Executions ({executions.length})</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
            <option value="confirmation_required">Needs Approval</option>
            <option value="rate_limited">Rate Limited</option>
          </select>
        </div>

        <div className="toolbar-right">
          <span className="record-count-text">
            Showing {filtered.length} of {executions.length} record(s)
          </span>
        </div>
      </div>

      {/* Execution Items Table/List */}
      {filtered.length === 0 ? (
        <EmptyState
          icon={<Filter size={24} />}
          title="No Executions Match Filter"
          description={`No execution records with status '${statusFilter}' were found.`}
        />
      ) : (
        <div className="execution-items-table">
          {filtered.map((item) => (
            <div
              key={item.execution_id}
              className="execution-row"
              onClick={() => onSelectExecution(item)}
            >
              <div className="execution-row-left">
                <div className="execution-id-line">
                  <span className="execution-id-code">
                    {item.execution_id.slice(0, 18)}...
                  </span>
                  {getStatusBadge(item.execution_status)}
                </div>
                <div className="execution-request-text">
                  "{item.user_input}"
                </div>
                {item.intent && (
                  <div className="execution-intent-line">
                    <span>Intent: {item.intent}</span>
                    {item.plan && item.plan.length > 0 && (
                      <span className="steps-count-badge">
                        {item.plan.length} plan step(s)
                      </span>
                    )}
                  </div>
                )}
              </div>

              <div className="execution-row-right">
                <button
                  className="view-details-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelectExecution(item);
                  }}
                  type="button"
                >
                  <Eye size={13} />
                  <span>Inspect</span>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default ExecutionList;


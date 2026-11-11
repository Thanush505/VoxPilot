import React, { useState, useEffect, useCallback } from 'react';
import { RefreshCw } from 'lucide-react';
import apiClient from '../api/client';
import { ExecutionStatusResponse, ConversationInfo } from '../api/types';
import ExecutionList from '../components/executions/ExecutionList';
import ExecutionDetails from '../components/executions/ExecutionDetails';
import LoadingState from '../components/common/LoadingState';
import ErrorState from '../components/common/ErrorState';

interface ExecutionsPageProps {
  isBackendConnected: boolean;
}

export const ExecutionsPage: React.FC<ExecutionsPageProps> = ({ isBackendConnected }) => {
  const [executions, setExecutions] = useState<ExecutionStatusResponse[]>([]);
  const [selectedExecution, setSelectedExecution] = useState<ExecutionStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchExecutions = useCallback(async () => {
    if (!isBackendConnected) {
      setExecutions([]);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // 1. Get recent conversations
      const convRes = await apiClient.getConversations(30, 0);
      if (!convRes.ok || !convRes.data) {
        setError(convRes.error || 'Failed to list conversations from SQLite database.');
        setIsLoading(false);
        return;
      }

      const convs: ConversationInfo[] = convRes.data;
      if (convs.length === 0) {
        setExecutions([]);
        setIsLoading(false);
        return;
      }

      // 2. Fetch executions for each conversation
      const allExecutions: ExecutionStatusResponse[] = [];
      const execPromises = convs.map((c) =>
        apiClient.getConversationExecutions(c.conversation_id)
      );

      const execResults = await Promise.all(execPromises);
      execResults.forEach((res) => {
        if (res.ok && res.data) {
          allExecutions.push(...res.data);
        }
      });

      // Deduplicate executions by execution_id
      const uniqueMap = new Map<string, ExecutionStatusResponse>();
      allExecutions.forEach((exec) => {
        uniqueMap.set(exec.execution_id, exec);
      });

      // Sort newest executions first
      const sorted = Array.from(uniqueMap.values()).reverse();
      setExecutions(sorted);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error retrieving execution history.');
    } finally {
      setIsLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    fetchExecutions();
  }, [fetchExecutions]);

  return (
    <div className="executions-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Execution History & Telemetry</h1>
          <p className="page-description">
            Complete audit trail of all persistent LangGraph agent workflows and tool execution steps.
          </p>
        </div>

        <button
          className="refresh-btn"
          onClick={fetchExecutions}
          disabled={isLoading}
          type="button"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          <span>Reload History</span>
        </button>
      </div>

      {!isBackendConnected ? (
        <ErrorState
          title="Backend Service Offline"
          message="FastAPI backend is disconnected. Cannot retrieve persistent SQLite execution history."
          onRetry={fetchExecutions}
          isRetrying={isLoading}
        />
      ) : isLoading ? (
        <LoadingState
          title="Loading Execution History..."
          message="Retrieving workflow records and tool execution results from SQLite storage."
        />
      ) : error ? (
        <ErrorState
          title="Error Querying Execution Records"
          message={error}
          onRetry={fetchExecutions}
          isRetrying={isLoading}
        />
      ) : (
        <ExecutionList
          executions={executions}
          onSelectExecution={(exec) => setSelectedExecution(exec)}
          isLoading={isLoading}
        />
      )}

      {/* Detailed Modal/Drawer */}
      <ExecutionDetails
        execution={selectedExecution}
        onClose={() => setSelectedExecution(null)}
      />
    </div>
  );
};

export default ExecutionsPage;

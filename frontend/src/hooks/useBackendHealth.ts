/**
 * VoxPilot Frontend - useBackendHealth Hook
 * Manages periodic health-check polling, connection status, and emergency-stop latch state.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import apiClient from '../api/client';
import { HealthResponse, EmergencyStopStatus } from '../api/types';

export type BackendConnectionState = 'checking' | 'connected' | 'disconnected';

export interface UseBackendHealthReturn {
  connectionState: BackendConnectionState;
  health: HealthResponse | null;
  emergencyStatus: EmergencyStopStatus | null;
  lastChecked: Date | null;
  error: string | null;
  isLoading: boolean;
  refetch: () => Promise<void>;
}

export function useBackendHealth(pollIntervalMs: number = 15000): UseBackendHealthReturn {
  const [connectionState, setConnectionState] = useState<BackendConnectionState>('checking');
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [emergencyStatus, setEmergencyStatus] = useState<EmergencyStopStatus | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const isMountedRef = useRef<boolean>(true);

  const checkHealth = useCallback(async () => {
    setIsLoading(true);

    try {
      let [healthRes, stopRes] = await Promise.all([
        apiClient.getHealth(),
        apiClient.getEmergencyStopStatus(),
      ]);

      if (!healthRes.ok && isMountedRef.current) {
        await new Promise((r) => setTimeout(r, 400));
        [healthRes, stopRes] = await Promise.all([
          apiClient.getHealth(),
          apiClient.getEmergencyStopStatus(),
        ]);
      }

      if (!isMountedRef.current) return;

      setLastChecked(new Date());

      if (healthRes.ok && healthRes.data) {
        setHealth(healthRes.data);
        setConnectionState('connected');
        setError(null);

        if (stopRes.ok && stopRes.data) {
          setEmergencyStatus(stopRes.data);
        } else {
          setEmergencyStatus(null);
        }
      } else {
        setConnectionState('disconnected');
        setHealth(null);
        setEmergencyStatus(null);
        setError(healthRes.error || 'Backend service unreachable.');
      }
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      setConnectionState('disconnected');
      setHealth(null);
      setEmergencyStatus(null);
      setError(err instanceof Error ? err.message : 'Unknown connection error.');
      setLastChecked(new Date());
    } finally {
      if (isMountedRef.current) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    checkHealth();

    const timer = setInterval(() => {
      checkHealth();
    }, pollIntervalMs);

    return () => {
      isMountedRef.current = false;
      clearInterval(timer);
    };
  }, [checkHealth, pollIntervalMs]);

  return {
    connectionState,
    health,
    emergencyStatus,
    lastChecked,
    error,
    isLoading,
    refetch: checkHealth,
  };
}

export default useBackendHealth;


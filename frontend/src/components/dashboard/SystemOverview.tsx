import React from 'react';
import { Cpu, HardDrive, Info, Lock } from 'lucide-react';

export const SystemOverview: React.FC = () => {
  return (
    <div className="dashboard-card">
      <div className="dashboard-card-header">
        <div className="dashboard-card-title-group">
          <Cpu size={18} />
          <div>
            <h2 className="dashboard-card-title">Host System Overview</h2>
            <p className="dashboard-card-description">
              Hardware utilization and platform telemetry (OS, CPU, RAM).
            </p>
          </div>
        </div>
      </div>

      {/* Clear Unavailable State Banner as required by Phase 6 guidelines */}
      <div className="system-unavailable-notice">
        <div className="notice-icon">
          <Info size={20} />
        </div>
        <div className="notice-content">
          <div className="notice-title">System Metrics API Endpoint Not Exposed</div>
          <p className="notice-description">
            The VoxPilot backend includes a safe internal system monitoring tool (<code>get_system_info</code> via psutil) 
            accessible to the LangGraph AI planner, but does not currently expose an unauthenticated public REST endpoint 
            for live CPU and RAM polling. To maintain strict security boundaries and prevent unauthorized host profiling, 
            system metrics remain in an unpolled state until an authenticated telemetry route is approved.
          </p>
        </div>
      </div>

      {/* Disabled / Unavailable Metric Placeholders */}
      <div className="metrics-grid">
        <div className="metric-box disabled">
          <div className="metric-box-header">
            <span>Operating System</span>
            <Lock size={12} />
          </div>
          <div className="metric-box-value">—</div>
          <div className="metric-box-footer">Target: Windows 11 (Host)</div>
        </div>

        <div className="metric-box disabled">
          <div className="metric-box-header">
            <span>CPU Usage</span>
            <Lock size={12} />
          </div>
          <div className="metric-box-value">— %</div>
          <div className="metric-box-footer">Intel Core 5 205H</div>
        </div>

        <div className="metric-box disabled">
          <div className="metric-box-header">
            <span>RAM Utilization</span>
            <Lock size={12} />
          </div>
          <div className="metric-box-value">— %</div>
          <div className="metric-box-footer">DDR5 High-Speed Memory</div>
        </div>

        <div className="metric-box disabled">
          <div className="metric-box-header">
            <span>Total System Memory</span>
            <HardDrive size={14} />
          </div>
          <div className="metric-box-value">16.0 GB</div>
          <div className="metric-box-footer">Allocated Host RAM</div>
        </div>

        <div className="metric-box disabled">
          <div className="metric-box-header">
            <span>Available Memory</span>
            <Lock size={12} />
          </div>
          <div className="metric-box-value">— GB</div>
          <div className="metric-box-footer">Unpolled via REST</div>
        </div>
      </div>
    </div>
  );
};

export default SystemOverview;


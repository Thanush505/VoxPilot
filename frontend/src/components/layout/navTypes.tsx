import React from 'react';
import {
  LayoutDashboard,
  Bot,
  ListOrdered,
  ShieldCheck,
  Cpu,
  FileSpreadsheet,
  Settings,
} from 'lucide-react';

export interface NavItemConfig {
  id: string;
  label: string;
  icon: React.ReactNode;
  isFunctional: boolean;
  badge?: string;
}

export const NAV_ITEMS: NavItemConfig[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    icon: <LayoutDashboard size={18} />,
    isFunctional: true,
  },
  {
    id: 'agent',
    label: 'AI Agent',
    icon: <Bot size={18} />,
    isFunctional: true,
  },
  {
    id: 'executions',
    label: 'Executions',
    icon: <ListOrdered size={18} />,
    isFunctional: true,
  },
  {
    id: 'confirmations',
    label: 'Confirmations',
    icon: <ShieldCheck size={18} />,
    isFunctional: true,
  },
  {
    id: 'automation',
    label: 'Automation',
    icon: <Cpu size={18} />,
    isFunctional: true,
  },
  {
    id: 'audit',
    label: 'Audit Logs',
    icon: <FileSpreadsheet size={18} />,
    isFunctional: true,
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: <Settings size={18} />,
    isFunctional: true,
  },
];


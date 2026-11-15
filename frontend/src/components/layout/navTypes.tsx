import React from 'react';
import {
  LayoutDashboard,
  Bot,
  AppWindow,
  ListOrdered,
  ShieldCheck,
  Cpu,
  FileSpreadsheet,
  Settings,
} from 'lucide-react';

export type NavSectionId = 'workspace' | 'operations' | 'governance';

export interface NavItemConfig {
  id: string;
  label: string;
  icon: React.ReactNode;
  isFunctional: boolean;
  section: NavSectionId;
  badge?: string;
}

export const NAV_SECTIONS: Array<{ id: NavSectionId; label: string }> = [
  { id: 'workspace', label: 'Workspace' },
  { id: 'operations', label: 'Operations & Safety' },
  { id: 'governance', label: 'Governance & System' },
];

export const NAV_ITEMS: NavItemConfig[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    icon: <LayoutDashboard size={18} />,
    isFunctional: true,
    section: 'workspace',
  },
  {
    id: 'agent',
    label: 'AI Agent',
    icon: <Bot size={18} />,
    isFunctional: true,
    section: 'workspace',
  },
  {
    id: 'applications',
    label: 'Applications',
    icon: <AppWindow size={18} />,
    isFunctional: true,
    section: 'workspace',
  },
  {
    id: 'executions',
    label: 'Executions',
    icon: <ListOrdered size={18} />,
    isFunctional: true,
    section: 'operations',
  },
  {
    id: 'confirmations',
    label: 'Confirmations',
    icon: <ShieldCheck size={18} />,
    isFunctional: true,
    section: 'operations',
  },
  {
    id: 'automation',
    label: 'Automation',
    icon: <Cpu size={18} />,
    isFunctional: true,
    section: 'operations',
  },
  {
    id: 'audit',
    label: 'Audit Logs',
    icon: <FileSpreadsheet size={18} />,
    isFunctional: true,
    section: 'governance',
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: <Settings size={18} />,
    isFunctional: true,
    section: 'governance',
  },
];


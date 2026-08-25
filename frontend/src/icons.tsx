// Inline SVG icon set — original, stroke-based, theme-tinted (currentColor).
type P = { className?: string };
const I = (path: string, vb = "0 0 24 24") =>
  function Icon({ className }: P) {
    return (
      <svg className={className} viewBox={vb} width="16" height="16" fill="none"
        stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
        aria-hidden="true" dangerouslySetInnerHTML={{ __html: path }} />
    );
  };

export const LogoIcon = ({ className }: P) => (
  <svg className={className} viewBox="0 0 32 32" width="26" height="26" fill="none" aria-hidden="true">
    <path d="M16 2 L28 9 V23 L16 30 L4 23 V9 Z" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="16" cy="16" r="4.2" fill="currentColor" opacity="0.9" />
    <path d="M16 5.5 V11.8 M16 20.2 V26.5 M6.5 11 L11.5 14 M25.5 11 L20.5 14 M6.5 21 L11.5 18 M25.5 21 L20.5 18"
      stroke="currentColor" strokeWidth="1.2" opacity="0.55" />
  </svg>
);

export const ChatIcon = I('<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>');
export const ModelsIcon = I('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>');
export const PlusIcon = I('<path d="M12 5v14M5 12h14"/>');
export const SearchIcon = I('<circle cx="11" cy="11" r="7"/><path d="m21 21-4.35-4.35"/>');
export const PinIcon = I('<path d="M12 17v5"/><path d="M9 3h6l1 7 3 3H5l3-3z"/>');
export const StarIcon = I('<path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/>');
export const ArchiveIcon = I('<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>');
export const TrashIcon = I('<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14M10 11v6M14 11v6"/>');
export const EditIcon = I('<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>');
export const SendIcon = I('<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>');
export const StopIcon = I('<rect x="6" y="6" width="12" height="12" rx="2"/>');
export const CopyIcon = I('<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>');
export const CheckIcon = I('<path d="M20 6 9 17l-5-5"/>');
export const RefreshIcon = I('<path d="M3 12a9 9 0 0 1 15.36-6.36L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.36 6.36L3 16M3 21v-5h5"/>');
export const ZapIcon = I('<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>');
export const ShieldIcon = I('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>');
export const ChevronLeftIcon = I('<path d="m15 18-6-6 6-6"/>');
export const ChevronRightIcon = I('<path d="m9 18 6-6-6-6"/>');
export const ChevronDownIcon = I('<path d="m6 9 6 6 6-6"/>');
export const XIcon = I('<path d="M18 6 6 18M6 6l12 12"/>');
export const SettingsIcon = I('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>');
export const DownloadIcon = I('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>');
export const AlertIcon = I('<path d="m10.29 3.86-8.2 14.14A2 2 0 0 0 3.82 21h16.36a2 2 0 0 0 1.73-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01"/>');
export const CpuIcon = I('<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>');
export const UsersIcon = I('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>');
export const BotIcon = I('<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 8V4M8 4h8M9 14h.01M15 14h.01M9 18h6"/>');
export const ResearchIcon = I('<circle cx="12" cy="12" r="9"/><path d="m16.2 7.8-2 6.3-6.4 2.1 2-6.3z"/>');
export const FolderIcon = I('<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>');
export const GitIcon = I('<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><path d="M6 9v6M18 9a9 9 0 0 1-9 9"/>');
export const SparkIcon = I('<path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.2 2.2M16.2 16.2l2.2 2.2M5.6 18.4l2.2-2.2M16.2 7.8l2.2-2.2"/><circle cx="12" cy="12" r="3.2"/>');
export const ClockIcon = I('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>');
export const GaugeIcon = I('<path d="M12 15l4-6"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>');
export const DatabaseIcon = I('<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>');
export const CaretDownIcon = I('<path d="m8 10 4 4 4-4"/>');
export const TerminalIcon = I('<path d="m4 17 6-6-6-6M12 19h8"/>');
export const ToolsIcon = I('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>');

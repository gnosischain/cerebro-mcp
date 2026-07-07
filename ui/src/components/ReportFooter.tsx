import { useState } from "react";
import { Copy, Check, ExternalLink } from "lucide-react";

interface Props {
  fileUri?: string;
  timestamp: string;
  version?: string;
}

export function ReportFooter({
  fileUri,
  timestamp,
  version = "cerebro_mcp",
}: Props) {
  const [copied, setCopied] = useState(false);
  const isFileUri = fileUri?.startsWith("file://");

  return (
    <footer className="report-footer">
      <span className="cli">$</span>
      <span>generated_by cerebro</span>
      <span style={{ opacity: 0.4 }}>·</span>
      <span>{timestamp}</span>
      <span className="spacer" />
      {fileUri && isFileUri && (
        <button
          className="footer-action no-print"
          onClick={() => {
            navigator.clipboard.writeText(fileUri);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />} copy path
        </button>
      )}
      {fileUri && !isFileUri && (
        <a
          className="footer-action no-print"
          href={fileUri}
          target="_blank"
          rel="noopener noreferrer"
        >
          <ExternalLink size={12} /> open
        </a>
      )}
      <span>{version}</span>
    </footer>
  );
}

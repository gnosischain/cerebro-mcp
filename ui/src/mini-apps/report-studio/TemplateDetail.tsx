// Template detail: the copyable instruction, parameter legend, and the
// measured benchmark matrix. The copy is THE product action.

import { useMemo, useState } from "react";
import {
  TIER_LABELS,
  fillInstructions,
  formatCost,
  formatDuration,
  formatTokens,
  modelLabel,
  type InstructionTemplate,
} from "./model/catalog";

function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text).then(() => true, () => legacyCopy(text));
  }
  return Promise.resolve(legacyCopy(text));
}

/** execCommand fallback — the mini-app host may gate the async clipboard API. */
function legacyCopy(text: string): boolean {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

export function TemplateDetail({
  template,
  onBack,
  onSendToAgent,
}: {
  template: InstructionTemplate;
  onBack: () => void;
  onSendToAgent?: (instructions: string) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState(false);

  const text = useMemo(
    () => fillInstructions(template, values),
    [template, values],
  );

  const measurements = Object.entries(template.measurements);

  return (
    <div className="rst-detail">
      <div className="rst-detail-head">
        <button type="button" className="rst-back" onClick={onBack}>
          ← Templates
        </button>
        <span className={`rst-tier rst-tier--${template.tier}`}>
          {TIER_LABELS[template.tier]}
        </span>
      </div>

      <h2>{template.label}</h2>
      <p className="rst-detail-purpose">{template.purpose}</p>
      <p className="rst-detail-deliverable">
        <strong>You get:</strong> {template.deliverable}
      </p>
      {template.personas.length > 0 && (
        <p className="rst-detail-personas">
          <strong>Personas invoked:</strong> {template.personas.join(" → ")}
        </p>
      )}

      {template.params.length > 0 && (
        <div className="rst-params">
          <h4>Parameters</h4>
          <table>
            <thead>
              <tr><th>Placeholder</th><th>Meaning</th><th>Your value</th></tr>
            </thead>
            <tbody>
              {template.params.map((p) => (
                <tr key={p.name}>
                  <td><code>{`{{${p.name}}}`}</code></td>
                  <td>{p.description}</td>
                  <td>
                    <input
                      value={values[p.name] ?? ""}
                      placeholder={p.example}
                      onChange={(e) =>
                        setValues((v) => ({ ...v, [p.name]: e.target.value }))}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="rst-instructions">
        <div className="rst-instructions-bar">
          <h4>Instruction</h4>
          <div className="rst-instructions-actions">
            {onSendToAgent && (
              <button type="button" onClick={() => onSendToAgent(text)}>
                Send to agent
              </button>
            )}
            <button
              type="button"
              className="rst-copy"
              onClick={() => {
                void copyText(text).then((ok) => {
                  setCopied(ok);
                  if (ok) window.setTimeout(() => setCopied(false), 2000);
                });
              }}
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
        <pre>{text}</pre>
      </div>

      <div className="rst-bench">
        <h4>Measured performance</h4>
        {measurements.length === 0 ? (
          <p className="rst-bench-empty">
            Not yet measured — numbers appear here after the next benchmark run.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Delivery time</th>
                <th>Cost</th>
                <th>Tokens (out / cache-read)</th>
                <th>Turns</th>
                <th>Review</th>
                <th>Runs</th>
              </tr>
            </thead>
            <tbody>
              {measurements.map(([model, m]) => (
                <tr key={model}>
                  <td>{modelLabel(model)}</td>
                  <td>
                    {m.duration_ms
                      ? `${formatDuration(m.duration_ms.median)} (${formatDuration(m.duration_ms.min)}–${formatDuration(m.duration_ms.max)})`
                      : "—"}
                  </td>
                  <td>
                    {m.cost_usd
                      ? `${formatCost(m.cost_usd.median)} (${formatCost(m.cost_usd.min)}–${formatCost(m.cost_usd.max)})`
                      : "—"}
                  </td>
                  <td>
                    {m.tokens.out != null ? formatTokens(m.tokens.out) : "—"}
                    {" / "}
                    {m.tokens.cache_read != null ? formatTokens(m.tokens.cache_read) : "—"}
                  </td>
                  <td>{m.num_turns_median ?? "—"}</td>
                  <td>
                    {m.review_total
                      ? `${m.review_passed}/${m.review_total} passed adversarial review`
                      : "—"}
                  </td>
                  <td title={m.measured_at ? `measured ${m.measured_at}` : undefined}>
                    {m.delivered}/{m.n_runs}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {measurements.length > 0 && (
          <p className="rst-bench-note">
            Medians over {template.benchmark.runs} runs with min–max spread — a
            spread, not a distribution. Cache-read tokens are the tool schemas
            every fresh session pays; review = two adversarial reviewers (data
            discipline + statistical) trying to refute the deliverable.
          </p>
        )}
      </div>
    </div>
  );
}

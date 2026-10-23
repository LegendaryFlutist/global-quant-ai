import type { Capability } from "../types";

/** 明确区分已实现与计划能力，避免把离线研究当作Web实盘功能。 */
export default function CapabilitiesPage({
  capabilities,
}: {
  capabilities: Capability[];
}) {
  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">PLATFORM CAPABILITIES</span>
          <h1>模型与平台能力</h1>
          <p>已实现、离线已有与后续计划分别标明</p>
        </div>
      </header>
      <div className="capability-grid">
        {capabilities.map((c) => (
          <section className="panel" key={c.id}>
            <span className="eyebrow">PHASE {c.phase}</span>
            <h3>{c.name}</h3>
            <span
              className={
              "badge " + (c.status === "IMPLEMENTED" ? "positive" : "neutral")
              }
            >
              {c.status === "IMPLEMENTED" ? "已实现" : c.status === "PARTIAL" ? "部分已实现" : "后续计划"}
            </span>
            <p>{c.detail}</p>
          </section>
        ))}
      </div>
    </>
  );
}

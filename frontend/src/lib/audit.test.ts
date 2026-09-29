import { describe, expect, it } from "vitest";
import { AUDIT_ACTIONS, auditActionLabel } from "./audit";

describe("audit action labels", () => {
  it("name every action in plain words and keep unknown codes readable", () => {
    expect(auditActionLabel("job.created")).toBe("Reconciliation started");
    expect(auditActionLabel("match.reset")).toBe("Rejection withdrawn");
    expect(auditActionLabel("something.new")).toBe("something.new");
    for (const [code, label] of AUDIT_ACTIONS) {
      expect(label).not.toContain(".");
      expect(label[0]).toBe(label[0].toUpperCase());
      expect(code).toMatch(/^[a-z_]+\.[a-z_]+$/);
    }
  });
});

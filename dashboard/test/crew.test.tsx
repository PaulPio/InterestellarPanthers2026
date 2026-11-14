import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Events, History } from "../src/api/types.gen";
import { fixture, mockApi, polled, renderRoute, status } from "./helpers";

const base = fixture<History>("history.prv_6h");
const events = fixture<Events>("events.mixed");

function serve() {
  return mockApi((url) => {
    if (url.pathname === "/api/events") return events;
    if (url.pathname !== "/api/history") return undefined;
    return {
      ...base,
      metric: url.searchParams.get("metric"),
      crew: url.searchParams.get("crew"),
      limits: { yellow: null, red: null },
    };
  });
}

describe("crew page", () => {
  it("shows the member's header facts, six vitals against baseline and six charts", async () => {
    const calls = serve();
    renderRoute("/crew/W2", polled(status("all_green")));
    expect(screen.getByRole("heading", { level: 1, name: "W2" })).toBeInTheDocument();
    expect(screen.getByText("Simulator")).toBeInTheDocument();
    const vitals = await screen.findByTestId("crew-vitals");
    expect(within(vitals).getAllByRole("term")).toHaveLength(6);
    expect(vitals).toHaveTextContent("Pulse-rate variability");
    expect(vitals).toHaveTextContent(/baseline/);
    await waitFor(() => expect(screen.getAllByTestId(/^chart-/)).toHaveLength(6));
    const crews = new Set(
      calls
        .filter((c) => c.url.pathname === "/api/history")
        .map((c) => c.url.searchParams.get("crew")),
    );
    expect([...crews]).toEqual(["W2"]);
  });

  it("lists only that member's events", async () => {
    serve();
    renderRoute("/crew/W1", polled(status("all_green")));
    const log = await screen.findByRole("region", { name: "Recent events" });
    await waitFor(() => expect(within(log).queryByText("Loading.")).toBeNull());
    const mine = events.events.filter((e) => e.crew === "W1");
    const rows = within(log).queryAllByRole("listitem");
    expect(rows).toHaveLength(Math.min(mine.length, 40));
  });

  it("explains an unknown crew id", () => {
    serve();
    renderRoute("/crew/W9", polled(status("all_green")));
    expect(screen.getByRole("heading", { name: "Crew member not found" })).toBeInTheDocument();
  });

  it("never says HRV", async () => {
    serve();
    const { container } = renderRoute("/crew/W1", polled(status("all_green")));
    await screen.findByTestId("crew-vitals");
    expect(container.textContent).not.toMatch(/\bHRV\b/);
  });
});

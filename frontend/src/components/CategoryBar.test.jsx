import { fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { fetchCategories } from "../api/client";
import { useCategories } from "../hooks/useCategories";
import { CategoryBar } from "./CategoryBar";

vi.mock("../api/client", () => ({ fetchCategories: vi.fn() }));

const categories = [
  {
    key: "kink",
    name: "Kink",
    tag: "KINK",
    start: 10140,
    end: 16680,
    count: 5390,
    subs: [
      { key: "hogtied", name: "Hogtied", tag: "HOG", start: 13445, end: 14430, count: 980, parent: null },
      { key: "nfc", name: "NudeFightClub", tag: "NFC", start: 14000, end: 14100, count: 99, parent: "hogtied" },
    ],
  },
  { key: "combat", name: "Combat", tag: "FIGHT", start: 20000, end: 21000, count: 750, subs: [] },
];

beforeEach(() => {
  fetchCategories.mockReset();
});

describe("useCategories", () => {
  test("returns category data after a successful fetch", async () => {
    fetchCategories.mockResolvedValue({ channel: "-1001", categories });
    const { result } = renderHook(() => useCategories());

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.categories).toEqual(categories);
    expect(result.current.channel).toBe("-1001");
  });

  test("returns empty categories after a fetch failure", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    fetchCategories.mockRejectedValue(new Error("HTTP 500"));
    const { result } = renderHook(() => useCategories());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.categories).toEqual([]);
    expect(result.current.channel).toBeNull();
    expect(logSpy).toHaveBeenCalledWith("CATEGORY FETCH ERROR: HTTP 500");
    logSpy.mockRestore();
  });
});

function renderBar(overrides = {}) {
  const props = { categories, loading: false, selectedKey: null, onSelect: vi.fn(), ...overrides };
  return { props, ...render(<CategoryBar {...props} />) };
}

function openDropdown() {
  fireEvent.click(screen.getByRole("button", { name: /All videos/ }));
}

describe("CategoryBar", () => {
  test("renders major, sub, and nested rows with counts and ranges in source order", () => {
    renderBar();
    openDropdown();

    const rows = screen.getAllByRole("option");
    expect(rows[0]).toHaveTextContent("All videos");
    expect(rows[1]).toHaveTextContent("Kink");
    expect(rows[1]).toHaveTextContent("5,390");
    expect(rows[1]).toHaveTextContent("10140–16680");
    expect(rows[2]).toHaveTextContent("Hogtied");
    expect(rows[2]).toHaveTextContent("980");
    expect(rows[2]).toHaveTextContent("13445–14430");
    expect(rows[3]).toHaveTextContent("NudeFightClub");
    expect(rows[3]).toHaveClass("category-row-nested");
    expect(rows[4]).toHaveTextContent("Combat");
  });

  test("orders row spans as name, count, then range", () => {
    renderBar();
    openDropdown();

    const kinkRow = screen.getByRole("option", { name: /Kink/ });
    const spanClasses = [...kinkRow.querySelectorAll("span")].map((span) => span.className);
    expect(spanClasses).toEqual(["category-row-name", "category-row-count", "category-row-range"]);
  });

  test("search keeps a matching sub and its major while hiding unrelated rows", () => {
    renderBar();
    openDropdown();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "hog" } });

    expect(screen.getByRole("option", { name: /Kink/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Hogtied/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Combat/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /NudeFightClub/ })).not.toBeInTheDocument();
  });

  test("selects a subcategory and displays a removable chip", () => {
    const { props, rerender } = renderBar();
    openDropdown();
    fireEvent.click(screen.getByRole("option", { name: /Hogtied/ }));
    expect(props.onSelect).toHaveBeenCalledWith("hogtied");

    rerender(<CategoryBar {...props} selectedKey="hogtied" />);
    const filter = screen.getByRole("region", { name: "Video category filter" });
    const chip = filter.querySelector(".category-chip");
    expect(chip).toBeInTheDocument();
    expect(within(chip).getByText("Kink / Hogtied")).toBeInTheDocument();
    fireEvent.click(within(chip).getByRole("button", { name: "Clear category filter" }));
    expect(props.onSelect).toHaveBeenLastCalledWith(null);
  });

  test("renders nothing without categories", () => {
    const { container } = renderBar({ categories: [] });
    expect(container).toBeEmptyDOMElement();
  });
});

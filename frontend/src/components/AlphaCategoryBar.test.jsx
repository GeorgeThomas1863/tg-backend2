import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { AlphaCategoryBar } from "./AlphaCategoryBar";

// Deliberately NOT alphabetical in array/chronological order, and mixes case
// ("apple" vs "Banana Major" vs "Zeta Group") so a naive case-sensitive sort
// (or no sort at all) would produce a different row order than a correct
// case-insensitive alphabetical sort would.
const categories = [
  {
    key: "zeta",
    name: "Zeta Group",
    tag: "ZETA",
    start: 5000,
    end: 6000,
    count: 120,
    subs: [{ key: "apple-sub", name: "apple", tag: "APL", start: 5100, end: 5200, count: 40, parent: null }],
  },
  { key: "banana-major", name: "Banana Major", tag: "BAN", start: 1000, end: 2000, count: 300, subs: [] },
];

function renderBar(overrides = {}) {
  const props = { categories, loading: false, selectedKey: null, onSelect: vi.fn(), ...overrides };
  return { props, ...render(<AlphaCategoryBar {...props} />) };
}

function openDropdown() {
  fireEvent.click(screen.getByRole("button", { name: /All videos/ }));
}

describe("AlphaCategoryBar", () => {
  test("renders every major and sub in one case-insensitive alphabetical list", () => {
    renderBar();
    openDropdown();

    const rows = screen.getAllByRole("option");
    expect(rows[0]).toHaveTextContent("All videos");
    expect(rows[1]).toHaveTextContent("apple");
    expect(rows[2]).toHaveTextContent("Banana Major");
    expect(rows[3]).toHaveTextContent("Zeta Group");
  });

  test("a sub row shows its parent's name for context", () => {
    renderBar();
    openDropdown();

    const appleRow = screen.getByRole("option", { name: /apple/ });
    expect(appleRow).toHaveTextContent("apple");
    expect(appleRow).toHaveTextContent("Zeta Group");
  });

  test("renders counts for each row", () => {
    renderBar();
    openDropdown();

    expect(screen.getByRole("option", { name: /apple/ })).toHaveTextContent("40");
    expect(screen.getByRole("option", { name: /Banana Major/ })).toHaveTextContent("300");
    expect(screen.getByRole("option", { name: /^Zeta Group/ })).toHaveTextContent("120");
  });

  test("selecting a row calls onSelect with the category key", () => {
    const { props } = renderBar();
    openDropdown();
    fireEvent.click(screen.getByRole("option", { name: /Banana Major/ }));
    expect(props.onSelect).toHaveBeenCalledWith("banana-major");
  });

  test("the search box filters the alphabetical list", () => {
    renderBar();
    openDropdown();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ban" } });

    expect(screen.getByRole("option", { name: /Banana Major/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /apple/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Zeta Group/ })).not.toBeInTheDocument();
  });

  test("renders nothing without categories", () => {
    const { container } = renderBar({ categories: [] });
    expect(container).toBeEmptyDOMElement();
  });

  test("labels itself distinctly from the chronological category bar", () => {
    renderBar();
    expect(screen.getByRole("region", { name: "Video category filter, alphabetical" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^A-Z/ })).toBeInTheDocument();
  });

  test("marks only the selected row aria-selected", () => {
    renderBar({ selectedKey: "apple-sub" });
    fireEvent.click(screen.getByRole("button", { expanded: false }));

    expect(screen.getByRole("option", { name: /apple/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: /Banana Major/ })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("option", { name: "All videos" })).toHaveAttribute("aria-selected", "false");
  });

  test("marks All videos selected when no category is chosen", () => {
    renderBar();
    openDropdown();

    expect(screen.getByRole("option", { name: "All videos" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: /Banana Major/ })).toHaveAttribute("aria-selected", "false");
  });
});

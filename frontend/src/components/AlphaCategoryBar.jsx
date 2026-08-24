import { useEffect, useMemo, useRef, useState } from "react";

// Second, exploratory category dropdown (todo item 9): same data and the
// same selectedKey/onSelect contract as CategoryBar, but flattened into one
// case-insensitive alphabetical list instead of chronological major/sub
// groups, so subs need their parent's name inline for context.
export function AlphaCategoryBar({ categories, loading, selectedKey, onSelect }) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null);
  const searchRef = useRef(null);
  const selection = findSelection(categories, selectedKey);
  const entries = useMemo(() => flattenAlphabetically(categories), [categories]);
  const visibleEntries = useMemo(() => filterEntries(entries, query), [entries, query]);

  useEffect(() => {
    if (!isOpen) return undefined;
    searchRef.current?.focus();

    const closeOnEscape = (event) => {
      if (event.key === "Escape") setIsOpen(false);
    };
    const closeOnOutsideClick = (event) => {
      if (!rootRef.current?.contains(event.target)) setIsOpen(false);
    };

    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("mousedown", closeOnOutsideClick);
    };
  }, [isOpen]);

  if (loading || categories.length === 0) return null;

  const selectEntry = (key) => {
    onSelect(key);
    setIsOpen(false);
    setQuery("");
  };

  return (
    <section className="alpha-category-bar" ref={rootRef} aria-label="Video category filter, alphabetical">
      <style>{ALPHA_CATEGORY_STYLES}</style>
      <div className="alpha-category-combo">
        <button
          type="button"
          className="alpha-category-combo-button"
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          onClick={() => setIsOpen((open) => !open)}
          title={`A-Z ${selection?.label || "All videos"}`}
        >
          <span className="alpha-category-combo-label">A-Z {selection?.label || "All videos"}</span>
          <span className="alpha-category-combo-caret" aria-hidden="true">▾</span>
        </button>
        {isOpen && (
          <div className="alpha-category-dropdown">
            <input
              ref={searchRef}
              className="alpha-category-search"
              type="search"
              aria-label="Search categories"
              placeholder="Search categories…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <div role="listbox" aria-label="Categories, alphabetical">
              <AlphaCategoryRow
                entry={{ key: null, name: "All videos" }}
                isSelected={!selectedKey}
                onSelect={selectEntry}
              />
              {visibleEntries.map((entry) => (
                <AlphaCategoryRow
                  key={entry.key}
                  entry={entry}
                  isSelected={entry.key === selectedKey}
                  onSelect={selectEntry}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function AlphaCategoryRow({ entry, isSelected, onSelect }) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={isSelected}
      className={isSelected ? "alpha-category-row alpha-category-row-selected" : "alpha-category-row"}
      onClick={() => onSelect(entry.key)}
    >
      <span className="alpha-category-row-name">
        {entry.name}
        {entry.parentName && <span className="alpha-category-row-parent"> · {entry.parentName}</span>}
      </span>
      {typeof entry.count === "number" && <span className="alpha-category-row-count">{entry.count.toLocaleString()}</span>}
      {entry.start !== undefined && <span className="alpha-category-row-range">{entry.start}–{entry.end}</span>}
    </button>
  );
}

// Flattens majors and their subs into one alphabetical list (case-insensitive
// by display name via localeCompare) instead of CategoryBar's hierarchical
// major-then-subs grouping. This is the whole point of the second dropdown —
// "just want to see what this looks like" — so a bare A-Z list of every
// filter, majors and subs together, is the view that's actually different
// from the existing one.
function flattenAlphabetically(categories) {
  const entries = [];
  for (const category of categories) {
    entries.push({
      key: category.key,
      name: category.name,
      tag: category.tag,
      start: category.start,
      end: category.end,
      count: category.count,
      parentName: null,
    });
    for (const sub of category.subs || []) {
      entries.push({
        key: sub.key,
        name: sub.name,
        tag: sub.tag,
        start: sub.start,
        end: sub.end,
        count: sub.count,
        parentName: category.name,
      });
    }
  }
  entries.sort((a, b) => a.name.localeCompare(b.name));
  return entries;
}

function filterEntries(entries, rawQuery) {
  const query = rawQuery.trim().toLowerCase();
  if (!query) return entries;
  return entries.filter((entry) => matchesEntry(entry, query));
}

function matchesEntry(entry, query) {
  return `${entry.name || ""} ${entry.tag || ""} ${entry.parentName || ""}`.toLowerCase().includes(query);
}

function findSelection(categories, selectedKey) {
  if (!selectedKey) return null;
  for (const category of categories) {
    if (category.key === selectedKey) return { label: category.name };
    for (const sub of category.subs || []) {
      if (sub.key === selectedKey) return { label: `${category.name} / ${sub.name}` };
    }
  }
  return null;
}

const ALPHA_CATEGORY_STYLES = `
  .alpha-category-bar { position: relative; display: flex; align-items: center; gap: 9px; min-height: 48px; border-bottom: 1px solid var(--hairline); }
  .alpha-category-combo { position: relative; }
  .alpha-category-combo-button { min-width: 150px; max-width: 260px; padding: 7px 9px; border: 1px solid var(--hairline); text-align: left; display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  .alpha-category-combo-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .alpha-category-combo-caret { flex-shrink: 0; }
  .alpha-category-combo-button:hover { border-color: var(--muted); }
  .alpha-category-dropdown { position: absolute; z-index: 20; top: calc(100% + 5px); left: 0; width: min(420px, calc(100vw - 48px)); max-height: 390px; overflow: auto; padding: 8px; background: var(--paper); border: 1px solid var(--muted); box-shadow: 0 14px 32px #20212422; }
  .alpha-category-search { width: 100%; margin-bottom: 7px; padding: 7px 8px; color: var(--ink); background: var(--paper); border: 1px solid var(--hairline); font: inherit; }
  .alpha-category-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 10px; width: 100%; padding: 7px 8px; text-align: left; }
  .alpha-category-row:hover { background: var(--hover); }
  .alpha-category-row-selected { background: var(--hover); font-weight: 600; }
  .alpha-category-row-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .alpha-category-row-parent { color: var(--muted); }
  .alpha-category-row-count, .alpha-category-row-range { font: 11px var(--font-mono); white-space: nowrap; }
  .alpha-category-row-range { color: var(--muted); text-align: right; justify-self: end; }
  @media (max-width: 640px) { .alpha-category-combo-button { min-width: 180px; } }
`;

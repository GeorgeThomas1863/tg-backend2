import { useEffect, useMemo, useRef, useState } from "react";

export function CategoryBar({ categories, loading, selectedKey, onSelect }) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null);
  const searchRef = useRef(null);
  const selection = findSelection(categories, selectedKey);
  const visibleCategories = useMemo(
    () => filterCategories(categories, query),
    [categories, query],
  );

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

  const selectCategory = (key) => {
    onSelect(key);
    setIsOpen(false);
    setQuery("");
  };

  return (
    <section className="category-bar" ref={rootRef} aria-label="Video category filter">
      <style>{CATEGORY_STYLES}</style>
      <div className="category-combo">
        <button
          type="button"
          className="category-combo-button"
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          onClick={() => setIsOpen((open) => !open)}
        >
          {selection?.label || "All videos"} <span aria-hidden="true">▾</span>
        </button>
        {isOpen && (
          <div className="category-dropdown">
            <input
              ref={searchRef}
              className="category-search"
              type="search"
              aria-label="Search categories"
              placeholder="Search categories…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <div role="listbox" aria-label="Categories">
              <CategoryRow item={{ key: null, name: "All videos" }} onSelect={selectCategory} />
              {renderCategoryRows(visibleCategories, selectCategory)}
            </div>
          </div>
        )}
      </div>
      {selection && (
        <span className="category-chip">
          {selection.label}
          <button type="button" aria-label="Clear category filter" onClick={() => selectCategory(null)}>✕</button>
        </span>
      )}
    </section>
  );
}

function renderCategoryRows(categories, onSelect) {
  const rows = [];
  for (const category of categories) {
    rows.push(<CategoryRow key={category.key} item={category} isMajor onSelect={onSelect} />);
    for (const sub of category.visibleSubs) {
      rows.push(
        <CategoryRow
          key={sub.key}
          item={sub}
          isNested={Boolean(sub.parent)}
          onSelect={onSelect}
        />,
      );
    }
  }
  return rows;
}

function CategoryRow({ item, isMajor = false, isNested = false, onSelect }) {
  const classes = ["category-row"];
  if (isMajor) classes.push("category-row-major");
  if (isNested) classes.push("category-row-nested");

  return (
    <button type="button" role="option" aria-selected="false" className={classes.join(" ")} onClick={() => onSelect(item.key)}>
      {item.start !== undefined && <span className="category-row-range">{item.start}–{item.end}</span>}
      <span className="category-row-name">{item.name}</span>
      {typeof item.count === "number" && <span className="category-row-count">{item.count.toLocaleString()}</span>}
    </button>
  );
}

function filterCategories(categories, rawQuery) {
  const query = rawQuery.trim().toLowerCase();
  const results = [];
  for (const category of categories) {
    const majorMatches = matchesCategory(category, query);
    const visibleSubs = findVisibleSubs(category.subs || [], query, majorMatches);
    if (!majorMatches && visibleSubs.length === 0) continue;
    results.push({ ...category, visibleSubs });
  }
  return results;
}

function findVisibleSubs(subs, query, showAll) {
  if (!query || showAll) return orderSubs(subs);
  const visibleKeys = new Set();
  for (const sub of subs) {
    if (!matchesCategory(sub, query)) continue;
    visibleKeys.add(sub.key);
    if (sub.parent) visibleKeys.add(sub.parent);
  }
  const visible = [];
  for (const sub of orderSubs(subs)) {
    if (visibleKeys.has(sub.key)) visible.push(sub);
  }
  return visible;
}

function orderSubs(subs) {
  const ordered = [];
  const added = new Set();
  for (const sub of subs) {
    if (sub.parent) continue;
    ordered.push(sub);
    added.add(sub.key);
    for (const child of subs) {
      if (child.parent !== sub.key) continue;
      ordered.push(child);
      added.add(child.key);
    }
  }
  for (const sub of subs) {
    if (!added.has(sub.key)) ordered.push(sub);
  }
  return ordered;
}

function findSelection(categories, selectedKey) {
  if (!selectedKey) return null;
  for (const category of categories) {
    if (category.key === selectedKey) return { item: category, label: category.name };
    for (const sub of category.subs || []) {
      if (sub.key === selectedKey) return { item: sub, label: `${category.name} / ${sub.name}` };
    }
  }
  return null;
}

function matchesCategory(item, query) {
  if (!query) return true;
  return `${item.name || ""} ${item.tag || ""}`.toLowerCase().includes(query);
}

const CATEGORY_STYLES = `
  .category-bar { position: relative; display: flex; align-items: center; gap: 9px; min-height: 48px; border-bottom: 1px solid var(--hairline); }
  .category-combo { position: relative; }
  .category-combo-button { min-width: 220px; padding: 7px 9px; border: 1px solid var(--hairline); text-align: left; display: flex; justify-content: space-between; }
  .category-combo-button:hover { border-color: var(--muted); }
  .category-dropdown { position: absolute; z-index: 20; top: calc(100% + 5px); left: 0; width: min(420px, calc(100vw - 48px)); max-height: 390px; overflow: auto; padding: 8px; background: var(--paper); border: 1px solid var(--muted); box-shadow: 0 14px 32px #20212422; }
  .category-search { width: 100%; margin-bottom: 7px; padding: 7px 8px; color: var(--ink); background: var(--paper); border: 1px solid var(--hairline); font: inherit; }
  .category-row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; width: 100%; padding: 7px 8px 7px 24px; text-align: left; }
  .category-row:hover { background: var(--hover); }
  .category-row-major { padding-left: 8px; font-weight: 600; }
  .category-row-nested { padding-left: 40px; }
  .category-row-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .category-row-count, .category-row-range { font: 11px var(--font-mono); white-space: nowrap; }
  .category-row-range { color: var(--muted); }
  .category-chip { padding: 5px 8px; border: 1px solid color-mix(in srgb, var(--green) 55%, var(--hairline)); background: color-mix(in srgb, var(--green) 8%, var(--paper)); font-size: 11px; }
  .category-chip button { padding-left: 6px; color: var(--green); }
  @media (max-width: 640px) { .category-combo-button { min-width: 180px; } }
`;

// Two-way segmented toggle for message-id sort order. Message id is the
// only sort key the backend supports (it is chronological), so this is
// deliberately not a dropdown of many options — a bordered button pair
// matches the house look (see CategoryBar's combo button / chip borders)
// better than a native <select> would for a two-way choice.
export function SortControl({ value, onChange, disabled }) {
  const title = disabled ? "Search results are ordered by relevance" : undefined;

  return (
    <div className="sort-control" role="group" aria-label="Sort order" title={title}>
      <SortOption value="asc" label="Oldest first" current={value} disabled={disabled} onChange={onChange} />
      <SortOption value="desc" label="Newest first" current={value} disabled={disabled} onChange={onChange} />
    </div>
  );
}

function SortOption({ value, label, current, disabled, onChange }) {
  return (
    <button
      type="button"
      className="sort-control-btn"
      aria-pressed={value === current}
      disabled={disabled}
      onClick={() => onChange(value)}
    >
      {label}
    </button>
  );
}

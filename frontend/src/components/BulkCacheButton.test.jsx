import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, test, expect, vi, beforeEach } from "vitest";
import { BulkCacheButton } from "./BulkCacheButton";
import { cancelBatchCache, requestBatchCache } from "../api/client";

vi.mock("../api/client", () => ({
  requestBatchCache: vi.fn(),
  cancelBatchCache: vi.fn(),
}));

describe("BulkCacheButton", () => {
  beforeEach(() => {
    requestBatchCache.mockReset();
    cancelBatchCache.mockReset();
  });

  test("idle label names the selected category and its count", () => {
    render(
      <BulkCacheButton
        selectedCategory="wrestling"
        selectedCategoryLabel="DirtyWrestlingPit"
        videoCount={573}
        batch={null}
        searchActive={false}
      />,
    );

    expect(screen.getByRole("button", { name: "Cache all 573 · DirtyWrestlingPit" })).toBeInTheDocument();
  });

  test("with no category selected the label says it will cache the whole library", () => {
    render(
      <BulkCacheButton
        selectedCategory={null}
        selectedCategoryLabel={undefined}
        videoCount={undefined}
        batch={null}
        searchActive={false}
      />,
    );

    expect(screen.getByRole("button", { name: "Cache library" })).toBeInTheDocument();
  });

  test("the first click does not fire a request — it only arms the confirmation", () => {
    render(
      <BulkCacheButton
        selectedCategory="wrestling"
        selectedCategoryLabel="DirtyWrestlingPit"
        videoCount={573}
        batch={null}
        searchActive={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cache all 573 · DirtyWrestlingPit" }));

    expect(requestBatchCache).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Really cache 573 videos?" })).toBeInTheDocument();
  });

  test("the second click calls requestBatchCache with the category key", async () => {
    requestBatchCache.mockResolvedValue({ success: true, message: "Queued", queued: 573 });
    render(
      <BulkCacheButton
        selectedCategory="wrestling"
        selectedCategoryLabel="DirtyWrestlingPit"
        videoCount={573}
        batch={null}
        searchActive={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cache all 573 · DirtyWrestlingPit" }));
    fireEvent.click(screen.getByRole("button", { name: "Really cache 573 videos?" }));

    await waitFor(() => expect(requestBatchCache).toHaveBeenCalledWith("wrestling"));
  });

  test("while batch.active it shows progress from total/remaining and clicking cancels the pass", async () => {
    cancelBatchCache.mockResolvedValue({ success: true, message: "Cancelled" });
    render(
      <BulkCacheButton
        selectedCategory="wrestling"
        selectedCategoryLabel="DirtyWrestlingPit"
        videoCount={573}
        batch={{ active: true, total: 573, remaining: 161 }}
        searchActive={false}
      />,
    );

    expect(screen.getByText("Caching 412/573 …")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(cancelBatchCache).toHaveBeenCalledOnce());
  });

  test("a failed request surfaces its message", async () => {
    requestBatchCache.mockResolvedValue({ success: false, message: "Unknown category" });
    render(
      <BulkCacheButton
        selectedCategory="wrestling"
        selectedCategoryLabel="DirtyWrestlingPit"
        videoCount={573}
        batch={null}
        searchActive={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cache all 573 · DirtyWrestlingPit" }));
    fireEvent.click(screen.getByRole("button", { name: "Really cache 573 videos?" }));

    expect(await screen.findByText("Unknown category")).toBeInTheDocument();
  });

  test("disabled with an explanatory title while a search is active", () => {
    render(
      <BulkCacheButton
        selectedCategory="wrestling"
        selectedCategoryLabel="DirtyWrestlingPit"
        videoCount={573}
        batch={null}
        searchActive
      />,
    );

    const button = screen.getByRole("button", { name: "Cache all" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", expect.stringContaining("search"));
  });

  test("an active batch still shows progress and Cancel during a search", () => {
    render(
      <BulkCacheButton
        selectedCategory="wrestling"
        selectedCategoryLabel="DirtyWrestlingPit"
        videoCount={573}
        batch={{ active: true, total: 573, remaining: 161 }}
        searchActive
      />,
    );

    expect(screen.getByText("Caching 412/573 …")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
  });

  test("the armed state resets when the selected category changes", () => {
    const { rerender } = render(
      <BulkCacheButton
        selectedCategory="wrestling"
        selectedCategoryLabel="DirtyWrestlingPit"
        videoCount={573}
        batch={null}
        searchActive={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cache all 573 · DirtyWrestlingPit" }));
    expect(screen.getByRole("button", { name: "Really cache 573 videos?" })).toBeInTheDocument();

    rerender(
      <BulkCacheButton
        selectedCategory="combat"
        selectedCategoryLabel="Combat"
        videoCount={750}
        batch={null}
        searchActive={false}
      />,
    );

    expect(screen.getByRole("button", { name: "Cache all 750 · Combat" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Really cache/ })).not.toBeInTheDocument();
    expect(requestBatchCache).not.toHaveBeenCalled();
  });
});

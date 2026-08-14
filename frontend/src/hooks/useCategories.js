import { useEffect, useState } from "react";
import { fetchCategories } from "../api/client";

export function useCategories() {
  const [categories, setCategories] = useState([]);
  const [channel, setChannel] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadCategories() {
      try {
        const data = await fetchCategories();
        if (cancelled) return;
        setCategories(Array.isArray(data.categories) ? data.categories : []);
        setChannel(data.channel ?? null);
      } catch (error) {
        if (cancelled) return;
        console.log(`CATEGORY FETCH ERROR: ${error.message}`);
        setCategories([]);
        setChannel(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadCategories();
    return () => {
      cancelled = true;
    };
  }, []);

  return { categories, channel, loading };
}

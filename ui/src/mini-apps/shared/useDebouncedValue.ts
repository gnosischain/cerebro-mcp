import { useEffect, useState } from "react";

/**
 * Returns a debounced mirror of `value` that only updates after `delay` ms
 * of stillness. Use for numeric inputs / search boxes that should not fire
 * tool calls on every keystroke.
 *
 *   const [limit, setLimit] = useState(500);
 *   const debouncedLimit = useDebouncedValue(limit, 400);
 *   useEffect(() => { callTool("...", { limit: debouncedLimit }); }, [debouncedLimit]);
 */
export function useDebouncedValue<T>(value: T, delay = 400): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

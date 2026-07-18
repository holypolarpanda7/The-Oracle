/* Inline SVG symbol defs (rendered once) + a tiny <Icon> that references them.
   Ported from docs/design/oracle-composed.html. */

export const ABILITY_ICON: Record<string, string> = {
  STR: "i-str", DEX: "i-dex", CON: "i-con", INT: "i-int", WIS: "i-wis", CHA: "i-cha",
};

export function Icon({ id, className }: { id: string; className?: string }) {
  return (
    <svg className={className} aria-hidden="true"><use href={`#${id}`} /></svg>
  );
}

export function IconDefs() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <symbol id="i-str" viewBox="0 0 24 24">
        <g fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
          <rect x="4.5" y="3.5" width="15" height="7" rx="1.6" fill="currentColor" fillOpacity=".16" />
          <path d="M12 10.5V21" /><path d="M8 7h8" />
        </g>
      </symbol>
      <symbol id="i-dex" viewBox="0 0 24 24">
        <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 3a13 13 0 0 1 0 18" /><path d="M6 3v18" strokeWidth="1.1" />
          <path d="M3 12h17" /><path d="M17 8.5 20.5 12 17 15.5" /><path d="M3 12l2.5-2.2M3 12l2.5 2.2" />
        </g>
      </symbol>
      <symbol id="i-con" viewBox="0 0 24 24">
        <path d="M12 20.6C4.8 15 3.3 10.4 5.4 7.4 7 5.1 10 5.4 12 8c2-2.6 5-2.9 6.6-.6 2.1 3 .6 7.6-6.6 13.2Z"
          fill="currentColor" fillOpacity=".2" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      </symbol>
      <symbol id="i-int" viewBox="0 0 24 24">
        <g fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round">
          <path d="M12 6C10 4.4 6.2 4.4 4 5.6V18.4C6.2 17.2 10 17.2 12 18.8" fill="currentColor" fillOpacity=".12" />
          <path d="M12 6c2-1.6 5.8-1.6 8-.4V18.4C17.8 17.2 14 17.2 12 18.8" fill="currentColor" fillOpacity=".12" />
          <path d="M12 6.4V18.8" />
        </g>
      </symbol>
      <symbol id="i-wis" viewBox="0 0 24 24">
        <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
          <path d="M2.5 12C5.5 7.2 9 5.2 12 5.2s6.5 2 9.5 6.8c-3 4.8-6.5 6.8-9.5 6.8S5.5 16.8 2.5 12Z" />
          <circle cx="12" cy="12" r="3.1" fill="currentColor" fillOpacity=".3" />
        </g>
      </symbol>
      <symbol id="i-cha" viewBox="0 0 24 24">
        <g fill="none" stroke="currentColor" strokeLinecap="round">
          <circle cx="12" cy="12" r="4.6" fill="currentColor" fillOpacity=".2" strokeWidth="1.7" />
          <g strokeWidth="1.8"><path d="M12 1.6V4M12 20v2.4M1.6 12H4M20 12h2.4M4.4 4.4 6 6M18 18l1.6 1.6M19.6 4.4 18 6M6 18l-1.6 1.6" /></g>
        </g>
      </symbol>
      <symbol id="i-feat" viewBox="0 0 24 24">
        <path d="M12 2.5 14.6 9l6.9.3-5.4 4.3 1.9 6.7L12 16.9 6 20.3l1.9-6.7L2.5 9.3 9.4 9Z"
          fill="currentColor" fillOpacity=".2" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      </symbol>
      <symbol id="i-fire" viewBox="0 0 24 24">
        <path d="M12 2.5c1.2 4-2 5.5-2 8.5 0-2 3-2.4 3 .5 0 2-2 2.5-2 4.5C11 14 8 15 8 18c0 2.6 2.4 4 4 4-4 0-8-3-8-8C4 7.5 11.5 8 12 2.5Z"
          fill="currentColor" fillOpacity=".22" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      </symbol>
      <symbol id="i-arcane" viewBox="0 0 24 24">
        <g fill="none" stroke="currentColor" strokeLinecap="round">
          <circle cx="12" cy="12" r="8.5" strokeWidth="1.4" fill="currentColor" fillOpacity=".12" />
          <path strokeWidth="1.5" d="M12 3.5 20.5 17H3.5Z" />
        </g>
      </symbol>
    </svg>
  );
}

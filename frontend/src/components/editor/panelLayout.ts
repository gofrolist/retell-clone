/** Width of the agent editor's right-hand panel.
 *
 * Fixed rather than flexible, and shared by the Create tab's settings
 * accordions and the Simulation tab's test panel: both sit in the same slot,
 * so they must be the same width or switching tabs shifts the layout. The
 * main column takes whatever is left.
 */
export const SIDE_PANEL_WIDTH = "w-[360px] shrink-0";

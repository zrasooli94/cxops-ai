import HandoffDemo from "./handoff-demo";

export const metadata = {
  title: "Interactive Handoff Demo | CXOps AI",
  description: "Try a fictional human-handoff workflow preview; no customer data or real support actions.",
  robots: { index: false, follow: false },
};

export default function HandoffDemoPage() {
  return <HandoffDemo />;
}

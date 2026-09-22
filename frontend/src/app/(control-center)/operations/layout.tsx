import { Metadata } from "next";

export const metadata: Metadata = {
  title: "Service Operations",
};

export default function OperationsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}

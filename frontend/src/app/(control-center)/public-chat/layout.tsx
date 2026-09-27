import { Metadata } from "next";

export const metadata: Metadata = {
  title: "Public Chat",
};

export default function PublicChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
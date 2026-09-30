import PageShell from "@/components/PageShell";
import WrongDatesClient from "@/components/WrongDatesClient";

export const revalidate = 0;

export default function WrongDatesPage() {
  return (
    <PageShell
      title="Wrong Date Monitor"
      subtitle="Messages that told a lead the wrong next class start or next open house date — review and send a correction."
    >
      <WrongDatesClient />
    </PageShell>
  );
}

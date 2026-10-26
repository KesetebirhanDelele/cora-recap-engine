import PageShell from "@/components/PageShell";
import SignupIntakeClient from "@/components/SignupIntakeClient";

export const revalidate = 0;

export default function SignupIntakePage() {
  return (
    <PageShell
      title="Sign-up Intake"
      subtitle="People who sign up through the Refactored platform and land in GHL: Explorer vs Open House, and how many have an email or a phone number. Refreshed hourly."
    >
      <SignupIntakeClient />
    </PageShell>
  );
}

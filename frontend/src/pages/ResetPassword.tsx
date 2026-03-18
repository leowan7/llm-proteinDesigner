import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { AuthLayout } from "@/components/auth/AuthLayout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { api } from "@/lib/api";

const resetPasswordSchema = z.object({
  email: z.string().email("Enter a valid email address."),
});

type ResetPasswordFormValues = z.infer<typeof resetPasswordSchema>;

export function ResetPassword() {
  const [submitted, setSubmitted] = useState(false);

  const form = useForm<ResetPasswordFormValues>({
    resolver: zodResolver(resetPasswordSchema),
    defaultValues: {
      email: "",
    },
  });

  const { isSubmitting } = form.formState;

  async function onSubmit(values: ResetPasswordFormValues) {
    try {
      await api("/auth/reset-password", {
        method: "POST",
        body: { email: values.email },
      });
    } catch {
      // Always show success message to avoid email enumeration
    } finally {
      setSubmitted(true);
    }
  }

  const footer = (
    <Link to="/login" className="hover:text-foreground hover:underline">
      Back to sign in
    </Link>
  );

  return (
    <AuthLayout
      title="Reset your password"
      subtitle="Enter your email and we'll send you a reset link."
      footer={footer}
    >
      {submitted ? (
        <p className="text-sm text-muted-foreground">
          If an account with that email exists, a reset link has been sent.
        </p>
      ) : (
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input
                      type="email"
                      placeholder="you@example.com"
                      className="placeholder:text-muted-foreground/40"
                      disabled={submitted}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <Button
              type="submit"
              className="w-full min-h-[44px]"
              disabled={isSubmitting || submitted}
              aria-disabled={isSubmitting || submitted}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" />
                  Sending...
                </>
              ) : (
                "Send reset link"
              )}
            </Button>
          </form>
        </Form>
      )}
    </AuthLayout>
  );
}

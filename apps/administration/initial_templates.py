from __future__ import annotations

def _wrap_in_base_layout(
    *,
    title: str,
    preheader: str,
    hero_section: str,
    body_content: str,
    footer_disclaimer: str = (
        "This is an automated security notification sent by KampuLynk. "
        "If you did not request this, please ignore it."
    ),
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>{title}</title>
<style type="text/css">
  body, table, td, p, a {{
    -webkit-text-size-adjust: 100%;
    -ms-text-size-adjust: 100%;
  }}
  table {{
    border-collapse: collapse;
    mso-table-lspace: 0pt;
    mso-table-rspace: 0pt;
  }}
  img {{
    border: 0;
    outline: none;
    text-decoration: none;
    -ms-interpolation-mode: bicubic;
  }}
  .body-text {{
    word-wrap: break-word;
    overflow-wrap: break-word;
  }}
  .otp-wrapper {{
    width: 100% !important;
  }}
  .otp-outer-table {{
    width: 100% !important;
  }}
  .otp-card {{
    width: 100% !important;
    box-sizing: border-box !important;
  }}
  @media only screen and (max-width: 620px) {{
    .email-container {{
      width: 100% !important;
      max-width: 100% !important;
    }}
    .email-outer-pad {{
      padding-left: 16px !important;
      padding-right: 16px !important;
    }}
    .email-body-content {{
      padding-left: 18px !important;
      padding-right: 18px !important;
    }}
    .body-text,
    .muted-text {{
      width: 100% !important;
      max-width: 100% !important;
    }}
    .otp-wrapper {{
      padding-left: 18px !important;
      padding-right: 18px !important;
    }}
    .otp-digit {{
      padding-left: 4px !important;
      padding-right: 4px !important;
    }}
  }}
</style>
</head>
<body>
<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">
  {preheader}
  &#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;
</div>
<table width="100%" bgcolor="#E0E5ED" cellpadding="0" cellspacing="0">
<tr><td align="center" class="email-outer-pad" style="padding:24px 12px;">
<table class="email-container" width="100%" cellpadding="0" cellspacing="0" style="background:#fff;max-width:620px;width:100%;border-radius:10px;overflow:hidden;">
<tr><td height="5" style="background:linear-gradient(90deg,#00A74F,#4473BD);font-size:0;"></td></tr>

<!-- Header Section -->
<tr>
  <td class="header-pad" style="background-color:#ffffff;padding:16px 24px;border-bottom:1px solid #EFEFEF;">
    <table width="100%" cellspacing="0" cellpadding="0" role="presentation">
      <tr>
        <!-- Left: Logo -->
        <td valign="middle">
          <img
            src="{{{{logo_url}}}}"
            alt="KampuLynk"
            width="44"
            height="44"
            style="display:block;border:0;border-radius:8px;width:44px;height:44px;"
          >
        </td>
        <!-- Right: Brand Name -->
        <td align="right" valign="middle" style="white-space:nowrap;padding-left:16px;font-family:Arial,Helvetica,sans-serif;font-size:22px;font-weight:700;line-height:1;">
          <span style="color:#0F82C8;">Kampu</span><span style="color:#10B14B;">Lynk</span>
        </td>
      </tr>
    </table>
  </td>
</tr>

<!-- Hero Message -->
{hero_section}

<!-- Body Section -->
<tr>
<td class="email-body-content" align="center" style="background:#ffffff;padding:24px 28px 20px;font:14px Arial,Helvetica,sans-serif;color:#333;line-height:1.7;text-align:center;">
{body_content}
</td>
</tr>

<!-- Break Line / Divider -->
<tr>
<td style="background:#071A35;padding:0 28px;">
<div style="border-top:1px solid #1E3A5F;font-size:0;line-height:0;height:0;"></div>
</td>
</tr>

<!-- Footer -->
<tr>
<td style="background:#071A35;padding:22px 28px 26px;">
<p style="margin:0 0 14px;font:12px Arial,Helvetica,sans-serif;color:#94A3B8;line-height:1.7;">
{footer_disclaimer}
</p>

<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;">
  <tr>
    <td style="border-top:1px solid #1E3A5F; padding-top:14px;">
      <p style="margin:0;font:11px Arial,Helvetica,sans-serif;color:#64748B;">
        &#169; KampuLynk. All rights reserved.
      </p>
    </td>
  </tr>
</table>
</td>
</tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _default_hero(text: str) -> str:
    return f"""<tr>
<td align="center" style="background:#071A35;padding:30px 28px 26px;text-align:center;">
<div style="margin:0;font-family:Arial,Helvetica,sans-serif;text-align:center;color:#ffffff;font-size:15px;line-height:1.5;">
{text}
</div>
</td>
</tr>"""


def _greeting_hero(subtitle: str) -> str:
    return f"""<tr>
<td align="center" style="background:#071A35;padding:30px 28px 26px;text-align:center;">
<p style="margin:0 0 10px;font-size:20px;font-weight:700;color:#FFFFFF;line-height:1.3;text-align:center;">Hello {{{{first_name}}}},</p>
<p style="margin:0;font-size:15px;color:#E2E8F0;font-weight:400;line-height:1.6;text-align:center;">{subtitle}</p>
</td>
</tr>"""


INITIAL_TEMPLATES: list[dict[str, str]] = [
    {
        "name": "otp_email",
        "subject": "Your KampuLynk Verification Code",
        "body_html": _wrap_in_base_layout(
            title="Verification Code",
            preheader="Please use this one-time code to verify your email address and securely access your account.",
            hero_section=_greeting_hero("Please use this one-time code to verify your email address and securely access your account."),
            body_content="""<p style="margin:0 0 14px;text-align:center;">Hello {{first_name}}</p>
<p style="margin:0 0 14px;text-align:center;">Please use this one-time code to verify your email address and securely access your account.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:4px 0 16px;">
  <tr>
    <td align="center" class="otp-wrapper" style="text-align:center;padding:0;width:100%;">
      {{displayotp}}
    </td>
  </tr>
</table>
<p class="muted-text" style="margin:0 0 4px;font-size:13px;color:#64748B;font-family:Arial,Helvetica,sans-serif;line-height:1.6;text-align:center;">
  The one-time code will expire in {{otp_minutes}} minutes.
</p>
<p style="margin:14px 0 0;font-size:13px;color:#64748B;font-family:Arial,Helvetica,sans-serif;line-height:1.6;text-align:center;">
  This is an automated security notification sent by KampuLynk. If you did not request this, please ignore this email.
</p>""",
        ),
    },
    {
        "name": "reset_password_email",
        "subject": "Reset Your KampuLynk Password",
        "body_html": _wrap_in_base_layout(
            title="Reset Your Password",
            preheader="Reset your KampuLynk account password",
            hero_section=_greeting_hero("We received a request to reset the password for your KampuLynk account. Click the button below to choose a new password."),
            body_content="""<!-- Primary CTA Button Component -->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin: 8px auto 20px; width: auto;">
  <tr>
    <td align="center" bgcolor="#0B5FA5" style="border-radius: 8px; background-color: #0B5FA5;">
      <a href="{{reset_link}}" target="_blank" style="font-size: 15px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-weight: 600; color: #ffffff; text-decoration: none; display: inline-block; padding: 12px 32px; border-radius: 8px; border: 1px solid #0B5FA5;">
        Reset Password
      </a>
    </td>
  </tr>
</table>

<!-- Direct Link Fallback Component -->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; margin-bottom: 20px;">
  <tr>
    <td style="padding: 14px 16px; font-size: 13px; line-height: 1.5; color: {{text_secondary}}; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
      <div style="font-weight: 600; color: {{text_primary}}; margin-bottom: 6px;">Having trouble with the button?</div>
      Copy and paste this link into your browser:
      <div style="margin-top: 8px; word-break: break-all; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; color: #0B5FA5; background: #ffffff; border: 1px solid #E2E8F0; padding: 8px 10px; border-radius: 4px;">
        <a href="{{reset_link}}" target="_blank" style="color: #0B5FA5; text-decoration: none; word-break: break-all;">{{reset_link}}</a>
      </div>
    </td>
  </tr>
</table>

<p style="margin: 0 0 8px 0; font-size: 13px; line-height: 1.6; color: {{text_secondary}}; text-align: center;">
  This password reset link is valid for {{password_reset_expire_minutes}} minutes.
</p>""",
        ),
    },
    {
        "name": "account_created_email",
        "subject": "KampuLynk Account Created",
        "body_html": _wrap_in_base_layout(
            title="Account Created Successfully",
            preheader="Welcome to KampuLynk",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 700; color: {{text_primary}}; letter-spacing: -0.5px; line-height: 1.3; text-align: center;">
  Welcome to KampuLynk!
</h1>

<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: {{text_secondary}}; text-align: center;">
  {{greeting}} Your KampuLynk account has been successfully created. We are thrilled to welcome you to our global student community.
</p>

<!-- Onboarding Feature Cards -->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin-bottom: 32px;">
  <tr>
    <td style="padding: 16px; background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 12px; margin-bottom: 12px; display: block;">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
        <tr>
          <td valign="top" style="font-size: 20px; line-height: 1; padding-right: 12px; width: 24px;">
            🤝
          </td>
          <td valign="top" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <div style="font-size: 15px; font-weight: 700; color: {{text_primary}}; margin-bottom: 4px;">Connect with Peers</div>
            <div style="font-size: 13px; line-height: 1.5; color: {{text_secondary}};">Find and interact with verified classmates, alumni, and students at your university or worldwide.</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
  <tr><td height="12" style="font-size: 0; line-height: 0;">&nbsp;</td></tr>
  <tr>
    <td style="padding: 16px; background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 12px; margin-bottom: 12px; display: block;">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
        <tr>
          <td valign="top" style="font-size: 20px; line-height: 1; padding-right: 12px; width: 24px;">
            📚
          </td>
          <td valign="top" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <div style="font-size: 15px; font-weight: 700; color: {{text_primary}}; margin-bottom: 4px;">Learn and Collaborate</div>
            <div style="font-size: 13px; line-height: 1.5; color: {{text_secondary}};">Join study rooms, access resources, and seek guidance from upperclassmen and mentors.</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
  <tr><td height="12" style="font-size: 0; line-height: 0;">&nbsp;</td></tr>
  <tr>
    <td style="padding: 16px; background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 12px; display: block;">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
        <tr>
          <td valign="top" style="font-size: 20px; line-height: 1; padding-right: 12px; width: 24px;">
            🚀
          </td>
          <td valign="top" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <div style="font-size: 15px; font-weight: 700; color: {{text_primary}}; margin-bottom: 4px;">Grow Your Network</div>
            <div style="font-size: 13px; line-height: 1.5; color: {{text_secondary}};">Explore internships, careers, and project collaborations designed specifically for university students.</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>

<!-- Primary CTA Button Component -->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin: 32px auto; width: auto;">
  <tr>
    <td align="center" bgcolor="#46B12F" style="border-radius: 8px; background-color: #46B12F;">
      <a href="{{base_url}}" target="_blank" style="font-size: 15px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-weight: 600; color: #ffffff; text-decoration: none; display: inline-block; padding: 12px 28px; border-radius: 8px; border: 1px solid #46B12F;">
        Get Started
      </a>
    </td>
  </tr>
</table>""",
        ),
    },
    {
        "name": "password_changed_email",
        "subject": "KampuLynk Password Changed",
        "body_html": _wrap_in_base_layout(
            title="Password Changed Successfully",
            preheader="Your password was changed successfully",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 700; color: {{text_primary}}; letter-spacing: -0.5px; line-height: 1.3; text-align: center;">
  Password Changed
</h1>

<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: {{text_secondary}}; text-align: center;">
  {{greeting}} Your password was changed successfully. This email is a notification to confirm that the update has been applied to your KampuLynk account.
</p>

<!-- Secondary Outlined Button Component -->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin: 24px auto; width: auto;">
  <tr>
    <td align="center" bgcolor="#ffffff" style="border-radius: 8px; border: 1px solid #E2E8F0;">
      <a href="{{base_url}}" target="_blank" style="font-size: 14px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-weight: 600; color: {{text_primary}}; text-decoration: none; display: inline-block; padding: 10px 24px; border-radius: 8px;">
        Go to Account Dashboard
      </a>
    </td>
  </tr>
</table>

<!-- Security Warning Alert -->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #FEF2F2; border: 1px solid #FEE2E2; border-radius: 10px; margin-top: 24px;">
  <tr>
    <td style="padding: 16px; font-size: 13px; line-height: 1.5; color: #991B1B; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
        <tr>
          <td valign="top" style="padding-right: 8px; font-size: 14px; line-height: 1;">
            ⚠️
          </td>
          <td valign="top" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
            <strong>Important Security Notice:</strong> If you did not change your password, please contact KampuLynk support immediately to secure your account and check for unauthorized login sessions.
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>""",
        ),
    },
    {
        "name": "email_verified_email",
        "subject": "KampuLynk Email Verified",
        "body_html": _wrap_in_base_layout(
            title="Email Verified Successfully",
            preheader="Your email address has been verified successfully",
            hero_section="""<tr>
<td align="center" style="background:#071A35;padding:30px 28px 26px;text-align:center;">
<div style="font-size:22px;font-weight:700;color:#ffffff;margin-bottom:8px;line-height:1.3;font-family:Arial,Helvetica,sans-serif;text-align:center;">Hello {{first_name}}</div>
<div style="font-size:17px;font-weight:400;color:#ffffff;line-height:1.6;font-family:Arial,Helvetica,sans-serif;text-align:center;">Your email address has been verified successfully.</div>
</td>
</tr>""",
            body_content="""<p style="margin:0;font-size:15px;line-height:1.7;color:{{text_primary}};text-align:center;">
Thank you for verifying your email address. You can now access all features of your KampuLynk account.
</p>""",
        ),
    },
    {
        "name": "lynkup_response_email",
        "subject": "KampuLynk Connection {{display_status}}",
        "body_html": _wrap_in_base_layout(
            title="Connection Request {{display_status}}",
            preheader="LynkUp Request {{response_status_title}}!",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 700; color: {{text_primary}}; letter-spacing: -0.5px; line-height: 1.3; text-align: center;">
  LynkUp Request {{response_status_title}}!
</h1>

<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color:#94A3B8; text-align: center;">
  {{greeting}} Your LynkUp request on KampuLynk has been {{response_status}}. We are thrilled to see you engaging with our global student community.
</p>

<!-- Sender Profile Card -->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin: 0 0 24px; border: 1px solid #E2E8F0; border-radius: 12px; background-color: #F8FAFC;">
  <tr>
    <td style="padding: 14px 16px;" width="64" valign="middle">
      <img
        src="{{sender_profile_photo_url}}"
        alt="{{sender_name}}"
        width="48"
        height="48"
        style="display: block; border-radius: 50%; width: 48px; height: 48px; object-fit: cover; border: 1px solid #E2E8F0;"
      >
    </td>
    <td style="padding: 14px 16px 14px 0;" valign="middle">
      <div style="font-size: 15px; font-weight: 700; color: #071A35; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.4;">
        {{sender_name}}
      </div>
      <div style="font-size: 13px; font-weight: 400; color: #64748B; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.4;">
        {{sender_meta_line}}
      </div>
    </td>
  </tr>
</table>""",
        ),
    },
    {
        "name": "connection_reminder_email",
        "subject": "People want to connect with you",
        "body_html": _wrap_in_base_layout(
            title="People want to connect with you",
            preheader="You have pending LynkUp requests",
            hero_section="""<tr>
<td align="center" style="background:#071A35;padding:28px 28px 26px;text-align:center;">
<p style="margin:0 0 10px;font-family:Arial,Helvetica,sans-serif;font-size:20px;font-weight:700;color:#FFFFFF;line-height:1.3;text-align:center;">{{greeting}}</p>
<p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#FFFFFF;font-weight:400;line-height:1.6;text-align:center;">{{instruction}}</p>
</td>
</tr>""",
            body_content="""<!-- Pending LynkUp request cards (profile info only; no CTAs) -->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin:0;text-align:left;">
  <tr>
    <td align="left" style="text-align:left;font-family:Arial,Helvetica,sans-serif;">
      {{request_cards_html}}
    </td>
  </tr>
</table>""",
        ),
    },
    {
        "name": "post_review_email",
        "subject": "{{subject}}",
        "body_html": _wrap_in_base_layout(
            title="{{title}}",
            preheader="{{title}}",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<div style="font-size:16px;font-weight:700;margin-bottom:16px;text-align:center;">{{title}}</div>
<p style="margin:0 0 14px;text-align:center;">{{greeting}}</p>
<p style="margin:0;text-align:center;">{{body}}</p>""",
        ),
    },
    {
        "name": "graduation_email",
        "subject": "Congratulations on your graduation!",
        "body_html": _wrap_in_base_layout(
            title="Congratulations on your graduation!",
            preheader="Congratulations on your graduation! 🎓",
            hero_section="""<tr>
<td align="center" style="background:#071A35;padding:30px 28px 26px;text-align:center;">
<p style="margin:0 0 10px;font-size:20px;font-weight:700;color:#FFFFFF;line-height:1.3;text-align:center;">Congratulations on your graduation! 🎓</p>
</td>
</tr>""",
            body_content="""<p style="margin:0 0 16px;font-size:15px;line-height:1.7;color:#333333;text-align:center;">
  You did it! Your graduation from the {{university_name}} marks an incredible milestone, and everyone at KampuLynk is excited to celebrate with you.
</p>

<p style="margin:0 0 16px;font-size:15px;line-height:1.7;color:#333333;text-align:center;">
  As you begin your next chapter, keep learning, connecting, and sharing your ideas with the world.
</p>""",
            footer_disclaimer='<span style="display:block;margin:0;font-family:Arial,Helvetica,sans-serif;font-size:20px;font-weight:700;color:#FFFFFF;line-height:1.3;text-align:center;">From your friends at KampuLynk 😊</span>',
        ),
    },
    {
        "name": "data_export_ready_email",
        "subject": "Your KampuLynk Data Export Is Ready",
        "body_html": _wrap_in_base_layout(
            title="Your KampuLynk Data Export Is Ready",
            preheader="Your Data Export Is Ready",
            hero_section=_greeting_hero("Your Data Export Is Ready"),
            body_content="""<p style="margin:0 0 12px 0;font-size:15px;line-height:1.6;color:{{text_secondary}};text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  Your KampuLynk data export has been generated successfully.
</p>

<p style="margin:0 0 28px 0;font-size:15px;line-height:1.6;color:{{text_secondary}};text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  Your encrypted ZIP archive is ready for download.
</p>

<!-- Primary CTA: visible label only; raw URL lives solely in href. -->
<table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin:0 auto 32px;width:auto;">
  <tr>
    <td align="center" bgcolor="{{brand_green}}" style="border-radius:8px;background-color:{{brand_green}};">
      <a href="{{download_url}}" target="_blank" style="font-size:15px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-weight:600;color:#ffffff;text-decoration:none;display:inline-block;padding:14px 32px;border-radius:8px;border:1px solid {{brand_green}};">
        Download Your Data
      </a>
    </td>
  </tr>
</table>

<p style="margin:0 0 10px 0;font-size:13px;font-weight:600;color:{{brand_blue}};letter-spacing:1.5px;text-transform:uppercase;text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  ZIP Password
</p>

<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin:0 0 16px;">
  <tr>
    <td align="center" style="padding:18px 16px;background-color:#F8FAFC;border:2px dashed {{brand_blue}};border-radius:12px;">
      <span style="font-size:22px;font-weight:700;color:{{text_primary}};font-family:'Courier New',Courier,monospace;letter-spacing:4px;">{{zip_password}}</span>
    </td>
  </tr>
</table>

<p style="margin:0 0 24px 0;font-size:14px;line-height:1.6;color:{{text_secondary}};text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  Your ZIP archive is password-protected. Use the password above to open the archive.
</p>

<p style="margin:0 0 8px 0;font-size:13px;line-height:1.5;color:{{text_secondary}};text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  File: {{zip_filename}}
</p>

<p style="margin:0 0 4px 0;font-size:14px;line-height:1.6;color:{{text_secondary}};text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  Download link expires on:
</p>

<p style="margin:0 0 28px 0;font-size:15px;font-weight:600;line-height:1.5;color:{{text_primary}};text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  {{download_expires_at}}
</p>

<p style="margin:0;font-size:13px;line-height:1.6;color:{{text_secondary}};text-align:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  For your security, this email was sent automatically. If you did not request this export, you can safely ignore this email.
</p>""",
        ),
    },
    {
        "name": "notification_send_email",
        "subject": "{{subject}}",
        "body_html": _wrap_in_base_layout(
            title="{{title}}",
            preheader="{{title}}",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<div style="font-size:16px;font-weight:700;margin-bottom:16px;">{{title}}</div>
{{notification_body}}""",
        ),
    },
    {
        "name": "notification_topic_email",
        "subject": "{{subject}}",
        "body_html": _wrap_in_base_layout(
            title="{{title}}",
            preheader="{{title}}",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<div style="font-size:16px;font-weight:700;margin-bottom:16px;">{{title}}</div>
{{notification_body}}""",
        ),
    },
    {
        "name": "notification_broadcast_email",
        "subject": "{{subject}}",
        "body_html": _wrap_in_base_layout(
            title="{{title}}",
            preheader="{{title}}",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<div style="font-size:16px;font-weight:700;margin-bottom:16px;">{{title}}</div>
{{notification_body}}""",
        ),
    },
    {
        "name": "notification_configuration_email",
        "subject": "{{subject}}",
        "body_html": _wrap_in_base_layout(
            title="{{title}}",
            preheader="{{title}}",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<div style="font-size:16px;font-weight:700;margin-bottom:16px;">{{title}}</div>
{{notification_body}}""",
        ),
    },
    {
        "name": "notification_resend_otp_email",
        "subject": "{{subject}}",
        "body_html": _wrap_in_base_layout(
            title="{{title}}",
            preheader="{{title}}",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<div style="font-size:16px;font-weight:700;margin-bottom:16px;">{{title}}</div>
{{notification_body}}""",
        ),
    },
    {
        "name": "notification_user_creation_email",
        "subject": "{{subject}}",
        "body_html": _wrap_in_base_layout(
            title="{{title}}",
            preheader="{{title}}",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<div style="font-size:16px;font-weight:700;margin-bottom:16px;">{{title}}</div>
{{notification_body}}""",
        ),
    },
    {
        "name": "bulk_campaign_email",
        "subject": "{{subject}}",
        "body_html": _wrap_in_base_layout(
            title="{{name}}",
            preheader="{{subject}}",
            hero_section=_default_hero("{{subject}}"),
            body_content="""<div style="text-align:left;font:14px Arial,Helvetica,sans-serif;color:#333;line-height:1.7;">
{{campaign_body_html}}
</div>""",
            footer_disclaimer='<span style="display:block;margin:0;font-family:Arial,Helvetica,sans-serif;text-align:center;color:#ffffff;font-size:15px;line-height:1.5;">From your friends at KampuLynk 😊</span>',
        ),
    },
    {
        "name": "temporary_password_email",
        "subject": "KampuLynk Account Created",
        "body_html": _wrap_in_base_layout(
            title="KampuLynk Account Created",
            preheader="Welcome to KampuLynk",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<div style="font-size:16px;font-weight:700;margin-bottom:16px;text-align:center;">Welcome to KampuLynk</div>
<p style="margin:0 0 14px;text-align:center;">{{greeting}}</p>
<p style="margin:0 0 14px;text-align:center;">Your KampuLynk account{{role_text}} has been created by an administrator.</p>
<p style="margin:0 0 10px;text-align:center;">Use this temporary password to sign in:</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin:8px 0 16px;">
<tr><td align="center" style="padding:18px 16px;background-color:#F8FAFC;border:1px dashed #CBD5E1;">
<span style="font-size:20px;font-weight:700;color:#071A35;font-family:'Courier New', Courier, monospace;">{{temporary_password}}</span>
</td></tr></table>
<p style="margin:0;text-align:center;">Please change this password after your first sign-in.</p>""",
        ),
    },
    {
        "name": "profile_updated_email",
        "subject": "KampuLynk Profile Updated",
        "body_html": _wrap_in_base_layout(
            title="Profile Updated",
            preheader="Profile Updated Successfully",
            hero_section=_default_hero("Our mission is to connect and empower university students to achieve their educational goals."),
            body_content="""<div style="font-size:16px;font-weight:700;margin-bottom:16px;text-align:center;">Profile Updated Successfully</div>
<p style="margin:0 0 14px;text-align:center;">{{greeting}}</p>
<p style="margin:0 0 14px;text-align:center;">Your KampuLynk profile has been successfully updated.</p>
<p style="margin:0;text-align:center;">If you did not perform this change, please contact support immediately.</p>""",
        ),
    },
]

# App passwords

App passwords are credentials you create for the Subsonic clients (phone app, desktop player, speakers...).

They can be:

- used as the **API key** (`apiKey`), which is the recommended way
- used as the **password** of a legacy login (`p`, or `t`+`s` tokens), if [`legacy_auth`](../configuration.md#legacy_auth) is enabled.

## Creating one

- **Web UI**: on your account page, or in the admin panel's **Connections** tab. The secret is shown _once_:, copy it.
- **Command line**: see [`create-app-password`](../usage/cli.md#creating-an-app-password).

## Access levels

| Access      | Allows                                                                                          |
|-------------|-------------------------------------------------------------------------------------------------|
| Full access | Everything (whihin the user's [capabilities](./accounts-and-permissions.md#user-roles))         |
| Read only   | Browsing, searching and streaming. Anything that changes data is refused with Subsonic error 50 |

## Notes

- Changing your account password does **not** revoke your app passwords.

- Wrong app passwords count towards the [rate limiter](./security.md) like any failed login.

- The secrets are stored encrypted, with the server key (`BEETSTREAMNEXT_KEY`), because the legacy `t`+`s` scheme needs the original secret. They are never shown again, never logged, and never part of the [account data export](./accounts-and-permissions.md) (only the name, access, and dates are).

- Upgrading from a **BeetstreamNext** version with a single API key per user: that key becomes an app password named **Default API key** with full access. It **only** works as an `apiKey` (not as a legacy password), since its secret was never stored in a recoverable form.

> [!IMPORTANT]
> Your account password still works with Subsonic clients (for now).
> 
> But giving it to clients is not ideal, and will be disabled in a future update. You should use app passwords instead.

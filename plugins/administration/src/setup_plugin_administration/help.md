# 🧩 Administration

Grant sudo access to an existing local login.

## 🔐 Grant sudo access

Choose an existing ordinary local account. Review its username, UID and the full administrative rights the Debian sudo group grants. Apply uses the native su/PAM prompt. A new login picks up the supplementary group after the policy and membership checks succeed. Closing the page keeps the grant in place.

**Verify effective access** checks an existing member’s effective sudo policy without modifying its account. Custom sudo providers or policies require manual review. After a lost reply, choose **Check interrupted operations**. Membership can be checked without repeating a grant. Effective-policy readback needs fresh native authorization.

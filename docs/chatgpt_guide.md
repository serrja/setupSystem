# ChatGPT Desktop

Select **Official ChatGPT Desktop** in Working environment. The Linux preview supports Debian 13 on amd64 and arm64. Use the application's sign-in window after installation. [OpenAI Linux installation guide](https://learn.chatgpt.com/docs/linux/linux-app.md)

The release catalog pins a versioned Debian archive for each supported architecture using the SHA-256 from the publisher's signed package index. The system adapter checks the downloaded bytes, Debian package name, version and architecture before APT sees the archive. It checks the installed version again after the offline package stage. The publisher package provisions its signed APT source for later security updates.

A download failure records its time and reason. Review the last attempt in Working environment and retry the same selection. A retry still requires the exact pinned digest. A new upstream version requires a reviewed release catalog update. The native application normally uses XWayland. Validate login and window behavior in the target desktop session. [OpenAI Linux compatibility](https://learn.chatgpt.com/docs/linux/linux-app.md)

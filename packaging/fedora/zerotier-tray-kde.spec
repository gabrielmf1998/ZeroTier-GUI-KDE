%global bin zerotier-tray

Name:           zerotier-tray-kde
Version:        1.0.3
Release:        1%{?dist}
Summary:        Unofficial tray icon to run and control ZeroTier One

License:        MIT
URL:            https://github.com/gabrielmf1998/ZeroTier-GUI-KDE
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

Requires:       python3
Requires:       python3-pyside6
Requires:       polkit
Requires:       systemd
Requires:       zerotier-one
Recommends:     firewalld
Recommends:     iproute
Recommends:     iputils

%description
An unofficial, non-affiliated tray GUI for ZeroTier One. Not made, endorsed or
supported by ZeroTier, Inc.; report bugs in the tray to its own project, never
to them.

ZeroTier Tray puts ZeroTier One in the system tray. Join and leave networks,
copy the address the controller gave you, watch who else on the network is
reachable and how far away they are, flip the per-network switches, start the
service, enable it at boot and open its UDP ports in firewalld - without ever
opening a terminal.

Everything on screen comes from zerotier-one's own local API on 127.0.0.1.
The four things that need root - the unit, the boot setting, the firewall and
the one-time copy of the service auth token - go through a small polkit helper.

26 icon styles including ZeroTier's own logo, 21 animations, a colour and an
animation per state, and a count badge for how many members are reachable.

%prep
%autosetup -n %{name}-%{version}

%install
install -d %{buildroot}%{_datadir}/%{name}/zerotiertray
install -m 0644 zerotiertray/*.py %{buildroot}%{_datadir}/%{name}/zerotiertray/
install -Dm 0755 packaging/%{bin} %{buildroot}%{_bindir}/%{bin}
install -Dm 0755 helper/%{bin}-helper %{buildroot}%{_libexecdir}/%{bin}-helper
install -Dm 0644 polkit/io.github.gabrielmf1998.zerotiertray.policy \
    %{buildroot}%{_datadir}/polkit-1/actions/io.github.gabrielmf1998.zerotiertray.policy
install -Dm 0644 packaging/%{bin}.desktop \
    %{buildroot}%{_datadir}/applications/%{bin}.desktop
install -Dm 0644 systemd/%{bin}.service \
    %{buildroot}%{_prefix}/lib/systemd/user/%{bin}.service
for s in 48 64 128 256 512; do
    install -Dm 0644 assets/%{bin}-${s}.png \
        %{buildroot}%{_datadir}/icons/hicolor/${s}x${s}/apps/%{bin}.png
done
install -Dm 0644 assets/%{bin}.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{bin}.svg
install -Dm 0644 LICENSE %{buildroot}%{_datadir}/licenses/%{name}/LICENSE
install -Dm 0644 README.md %{buildroot}%{_datadir}/doc/%{name}/README.md

%files
%license LICENSE
%doc README.md
%{_bindir}/%{bin}
%{_libexecdir}/%{bin}-helper
%{_datadir}/%{name}/
%{_datadir}/polkit-1/actions/io.github.gabrielmf1998.zerotiertray.policy
%{_datadir}/applications/%{bin}.desktop
%{_prefix}/lib/systemd/user/%{bin}.service
%{_datadir}/icons/hicolor/*/apps/%{bin}.*

%changelog
* Tue Sep 01 2026 Gabriel Marques Ferrarezi <110578985+gabrielmf1998@users.noreply.github.com> - 1.0.3-1
- Pick the icon and the animation by looking at them: live galleries of all 26
  shapes and all 21 animations, and every state drawn at once above the tabs

* Tue Sep 01 2026 Gabriel Marques Ferrarezi <110578985+gabrielmf1998@users.noreply.github.com> - 1.0.2-1
- Do not refuse to start when no system tray is up yet; wait for one
- Manual check for updates against the project's releases

* Tue Sep 01 2026 Gabriel Marques Ferrarezi <110578985+gabrielmf1998@users.noreply.github.com> - 1.0.1-1
- Stopping the service no longer leaves the unit in failed when the daemon
  crashes on the way down; restart always brings it back

* Tue Sep 01 2026 Gabriel Marques Ferrarezi <110578985+gabrielmf1998@users.noreply.github.com> - 1.0.0-1
- First release: join and leave networks, live member list, per-network flags,
  service and boot control, firewalld ports, 26 icon styles, 21 animations

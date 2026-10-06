"""Use the image's prewarmed Gradle cache in network-isolated evaluations."""
from pathlib import Path
import sysconfig

launcher = Path(sysconfig.get_paths()["purelib"]) / "minedojo/sim/Malmo/Minecraft/launchClient.sh"
source = launcher.read_text(encoding="utf-8")
if "./gradlew runClient --offline" not in source:
    if "./gradlew runClient --stacktrace" not in source:
        raise RuntimeError("Unexpected MineDojo launcher; cannot enable offline execution")
    source = source.replace("./gradlew runClient --stacktrace", "./gradlew runClient --offline --stacktrace")
source = source.replace("export GRADLE_USER_HOME=${runDir}/gradle", "export GRADLE_USER_HOME=/root/.gradle")
launcher.write_text(source, encoding="utf-8")

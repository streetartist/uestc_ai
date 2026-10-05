"""Repair MineDojo 0.1's retired JitPack MixinGradle coordinate.

Use the original plugin publisher's 0.6 repository, compatible with ForgeGradle
2.x; do not fetch community-hosted replacement JARs or disable TLS validation.
"""
from pathlib import Path
import sysconfig


path = Path(sysconfig.get_paths()["purelib"]) / "minedojo/sim/Malmo/Minecraft/build.gradle"
source = path.read_text(encoding="utf-8")
source = source.replace("com.github.SpongePowered:MixinGradle:dcfaf61", "org.spongepowered:mixingradle:0.6-SNAPSHOT")
source = source.replace("maven { url 'https://jitpack.io' }", "maven { url 'https://repo.spongepowered.org/repository/maven-public/' }\n        maven { url 'https://jitpack.io' }")
source = source.replace("jcenter()", "mavenCentral()")
source += """
// ForgeGradle 2.2 embeds an obsolete HTTP asset endpoint. Prepopulate its
// ordinary cache over HTTPS, verifying Minecraft's index size and SHA-1.
getAssets.doFirst { task ->
    project.exec {
        commandLine 'python3', '/runner/evaluation_adapters/minecraft/prepare_assets.py',
                    task.assetsIndex.toString(), task.assetsDir.toString()
    }
}
"""
path.write_text(source, encoding="utf-8")
